/*
 * XenRT: Small disk test utility. We write the sector number to
 * to each successive sector. The verify flag checks for incorrect
 * sector entries.
 *
 * Julian Chesterfield, July 2007
 *
 * Copyright (c) 2007 XenSource, Inc. All use and distribution of this
 * copyrighted material is governed by and subject to terms and
 * conditions as licensed by XenSource, Inc. All other rights reserved.
 *
 */


#ifndef _GNU_SOURCE
  #define _GNU_SOURCE
#endif
#include <stdbool.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <sys/stat.h>
#include <sys/ioctl.h>
#include <linux/fs.h>
#include <string.h>
#include <time.h>
#include "atomicio.h"

/* This tool is able to write test pattern to disk and verify it.
 * One sector, 512 bytes, is split to mutiple slices. And there 
 * are two numbers in a slice:
 *   - sector id
 *   - iterator, over the entire disk
 * This is the test pattern.
 * Each time the tool write/read just a block from disk, the block
 * is composed by mutiple sectors. User is allowed to specify the
 * maximum of blocks count or maximum elapsed time to control test
 * accomplishment.
 */

struct fd_state {
    unsigned long      sector_size; // size of a sector
    unsigned long long size;        // device size in sectors
    unsigned long long size_sects;  // total sectors
    unsigned long long fullsize;    // full size of the device
};

struct sector_slice {
    unsigned long long sect;
    unsigned long long iter;
};

#define DEFAULT_SECTOR_SIZE 512
#define SECTOR_SHIFT 9
#define HEADERS_OF_SECTION (DEFAULT_SECTOR_SIZE / sizeof(struct sector_slice))

unsigned long long sects_of_block = 0;  // input: sector count of one block
unsigned long long max_blocks = 0;      // input: max blocks to write/read
unsigned long long max_time = 0;        // input: max time to test, in second
unsigned long long total_sects = 0;     // total secters
unsigned long long block_size = 0;      // block size in bytes
char *block_buf = NULL;                 // buffer of one block to each write/read
struct fd_state state = {0};            // device size info

unsigned long long sect = 0;            // current sector in writing/reading
unsigned long long iter = 0;            // input: current iterater for sector_slice(s)
unsigned long long sect_errors = 0;     // total verify errors of sectors


void usage(const char *cmd)
{
    fprintf(stderr, "usage: %s <op> <device> <block> <mass> <time> <iter>\n"
            "  op:     'write' or 'verify' test\n"
            "  device: device file\n"
            "  block:  number of sectors for one block, greater than 0. Note: one sector size is 512 bytes\n"
            "  mass:   max number of blocks for test, greater than 0\n"
            "  time:   max elapsed time to test, in seconds, 0 means unlimit\n"
            "  iter:   initial value for iterator\n"
            "\n"
            "return 0 when op executed successfully and output numbers:\n"
            "  max_blocks:  same to input <block>\n"
            "  op_blocks:   total number of blocks op-ed in practice\n"
            "  op_elapsed:  total elapsed time in practice\n"
            "  sect_errors: number of sectors with verify error\n"
            "\n"
            "examples:\n"
            "  # diskdatatest write /dev/sdb 512 1228956 15 1000\n"
            "  1228956 5989 15.004593 0\n"
            "  # diskdatatest verify /dev/sdb 512 5989 15 1000\n"
            "  5989 5989 6.733130 0\n"
            "\n"
            "  # diskdatatest write /dev/sdb 512 1228956 0 2000\n"
            "  1228956 1228956 3109.534673 0\n"
            "  # diskdatatest verify /dev/sdb 512 1228956 0 2000\n"
            "  1228956 1228956 2462.567301 0\n",
            cmd);
}

void init_params(int argc, char *argv[])
{
    if (argc != 7) {
        fprintf(stderr, "Parameter count is incorrect\n");
        usage(argv[0]);
        exit(1);
    }
    if (strcmp(argv[1], "write") && strcmp(argv[1], "verify")) {
        fprintf(stderr, "Unknown <op>\n");
        usage(argv[0]);
        exit(1);
    }
    
    sects_of_block  = strtoull(argv[3], NULL, 10);
    max_blocks      = strtoull(argv[4], NULL, 10);
    max_time        = strtoull(argv[5], NULL, 10);
    iter            = strtoull(argv[6], NULL, 10);
    if (sects_of_block < 1) {
        fprintf(stderr, "<block> is incorrect\n");
        usage(argv[0]);
        exit(1);
    }
    if (max_blocks < 1) {
        fprintf(stderr, "<mass> is incorrect\n");
        usage(argv[0]);
        exit(1);
    }
    
    block_size = sects_of_block * DEFAULT_SECTOR_SIZE;
    total_sects = max_blocks * sects_of_block;
}

void alloc_block_buf()
{
    block_buf = malloc(block_size);
    if (!block_buf) 
    {
        fprintf(stderr, "Malloc block buffer failed\n");
        exit(1);
    }
}

void free_block_buf()
{
    if (block_buf)
        free(block_buf);
    block_buf = NULL;
}

inline void update_sect(char *sect_buf)
{
    struct sector_slice *hdr = NULL;
    unsigned long long i = 0;
    for (; i < HEADERS_OF_SECTION; i++) {
        hdr = (struct sector_slice *)sect_buf + i;
        hdr->sect = sect;
        hdr->iter = iter++;
    }
}

inline void update_block()
{
    unsigned long long i = 0;
    for (; i < sects_of_block; i++) {
        update_sect(block_buf + i*DEFAULT_SECTOR_SIZE);
        
        if (!(sect % (512*1024*1024/DEFAULT_SECTOR_SIZE))) // logging per 512MB
        {
            printf("Writing sector %llx of %llx\n", sect, total_sects);
        }
        sect++;
    }
}

inline void verify_sect(const char *sect_buf)
{
    const struct sector_slice *hdr = NULL;
    bool sect_error = false;
    unsigned long long i = 0;
    for (; i < HEADERS_OF_SECTION; i++) {
        hdr = (const struct sector_slice *)sect_buf + i;
        if (hdr->sect != sect) {
            sect_error = true;
            if (sect_errors < 5) {     // only logging first 5 details
                fprintf(stderr, "Unmatched sector %llu for %llu:\n", hdr->sect, sect);
            }
        }
        if (hdr->iter != iter) {
            sect_error = true;
            if (sect_errors < 5) {     // only logging first 5 details
                fprintf(stderr, "Unmatched iter %llu for %llu:\n", hdr->iter, iter);
            }
        }
        iter++;
    }
    
    if (sect_error)
        sect_errors++;
}

inline void verify_block()
{
    unsigned long long i = 0;
    for (; i < sects_of_block; i++) {
        verify_sect(block_buf + i*DEFAULT_SECTOR_SIZE);

        if (!(sect % (512*1024*1024/DEFAULT_SECTOR_SIZE))) // logging per 512MB
        {
            printf("Verifying sector %llx of %llx\n", sect, total_sects);
        }
        sect++;
    }
}

static int getsize(int fd, struct fd_state *s)
{
    struct stat stat;
    int ret;

    ret = fstat(fd, &stat);
    if (ret != 0) {
        fprintf(stderr, "ERROR: fstat failed, Couldn't stat image\n");
        return -EINVAL;
    }

    if (S_ISBLK(stat.st_mode)) {
        /*Accessing block device directly*/
        s->size = 0;
        if (ioctl(fd,BLKGETSIZE,&s->size)!=0) {
            fprintf(stderr,"ERR: BLKGETSIZE failed, "
                    "couldn't stat image\n");
            return -EINVAL;
        }
        s->size_sects = s->size;
        /*Get the sector size*/
#if defined(BLKSSZGET)
        {
            s->sector_size = DEFAULT_SECTOR_SIZE;
            ioctl(fd, BLKSSZGET, &s->sector_size);
        }
#else
        s->sector_size = DEFAULT_SECTOR_SIZE;
#endif
        if (s->sector_size != DEFAULT_SECTOR_SIZE) {
            if (s->sector_size > DEFAULT_SECTOR_SIZE) {
                s->size_sects = (s->sector_size/DEFAULT_SECTOR_SIZE)*s->size;
            } else {
                s->size_sects = s->size/(DEFAULT_SECTOR_SIZE/s->sector_size);
            }
        }
        s->fullsize = s->sector_size * s->size;

    } else {
        /*Local file? try fstat instead*/
        s->size = (stat.st_size >> SECTOR_SHIFT);
        s->sector_size = DEFAULT_SECTOR_SIZE;
        s->size_sects = s->size;
        s->fullsize = stat.st_size;
    }

    return 0;
}

bool check_file_size(int fd)
{
    unsigned long long file_blocks = 0;
    if (getsize(fd, &state) != 0)
        return false;
    file_blocks = state.size_sects / sects_of_block;
    if (max_blocks > file_blocks) {
        fprintf(stderr, "Total blocks to test %llx exceeds file had %llx\n", max_blocks, file_blocks);
        return false;
    }
    return true;
}

inline double get_op_elapsed(const struct timeval *start)
{
    struct timeval current_time;
    gettimeofday(&current_time, NULL);
    return (current_time.tv_sec - start->tv_sec) + (current_time.tv_usec - start->tv_usec) / 1000000.0;
}

int op_testpattern(const char *file, bool op_write)
{
    int fd = -1;
    mode_t mode = O_LARGEFILE;
    unsigned long long pos, len, i, op_blocks;
    double op_elapsed;
    struct timeval start_time;
    
    mode |= op_write ? O_RDWR : O_RDONLY;
    fd = open(file, mode);
    if (fd == -1) {
        fprintf(stderr, "Unable to open %s, errno %d\n", file, errno);
        return 1;
    }
    
    if (!check_file_size(fd)) {
        close(fd);
        return 1;
    }
    
    gettimeofday(&start_time, NULL);

    for(i = 0; i < max_blocks; i++) {
        pos = i * block_size;
        if (lseek(fd, pos, SEEK_SET) == (off_t)-1) {
            fprintf(stderr, "Unable to seek to offset %llx\n", pos);
            close(fd);
            return 1;
        }

        if (op_write) {
            update_block();
            len = atomicio(vwrite, fd, block_buf, block_size);
            if (len < block_size) {
                fprintf(stderr, "Write block %llx failed\n", i);
                close(fd);
                return 1;
            }
        } else {
            len = atomicio(read, fd, block_buf, block_size);
            if (len < block_size) {
                fprintf(stderr, "Read block %llx failed\n", i);
                close(fd);
                return 1;
            }
            verify_block();
        }

        op_blocks = i + 1;
        op_elapsed = get_op_elapsed(&start_time);
        if (max_time > 0 && op_elapsed >= max_time)
            break;
    }
    
    printf("%llu %llu %f %llu\n", max_blocks, op_blocks, op_elapsed, sect_errors);
    
    close(fd);
    return 0;
}


int main(int argc, char *argv[])
{
    int ret;
    
    init_params(argc, argv);
    alloc_block_buf();

    ret = op_testpattern(argv[2], !strcmp(argv[1], "write"));
    
    free_block_buf();

    return ret;
}
