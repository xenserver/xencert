# Copyright (C) Citrix Systems Inc.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published
# by the Free Software Foundation; version 2.1 only.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301  USA
#
# nfs.py: NFS related utility functions

import util
import errno
import os
import time
# The algorithm for tcp and udp (at least in the linux kernel) for
# NFS timeout on softmounts is as follows:
#
# UDP:
# As long as the request wasn't started more than timeo * (2 ^ retrans)
# in the past, keep doubling the timeout.
#
# TCP:
# As long as the request wasn't started more than timeo * (1 + retrans)
# in the past, keep increaing the timeout by timeo.
#
# The time when the retrans may retry has been made will be:
# For udp: timeo * (2 ^ retrans * 2 - 1)
# For tcp: timeo * n! where n is the smallest n for which n! > 1 + retrans
#
# thus for retrans=1, timeo can be the same for both tcp and udp,
# because the first doubling (timeo*2) is the same as the first increment
# (timeo+timeo).

RPCINFO_BIN = "/usr/sbin/rpcinfo"
SHOWMOUNT_BIN = "/usr/sbin/showmount"

DEFAULT_NFSVERSION = '3'

NFS_VERSION = [
    'nfsversion', 'for type=nfs, NFS protocol version - 3, 4, 4.0, 4.1']

NFS_SERVICE_WAIT = 30
NFS_SERVICE_RETRY = 6


class NfsException(Exception):

    def __init__(self, errstr):
        self.errstr = errstr


def check_server_service(server):
    """Ensure NFS service is up and available on the remote server.

    Returns False if fails to detect service after
    NFS_SERVICE_RETRY * NFS_SERVICE_WAIT
    """

    retries = 0
    errlist = [errno.EPERM, errno.EPIPE, errno.EIO]

    while True:
        try:
            services = util.pread([RPCINFO_BIN, "-s", "%s" % server])
            services = services.split("\n")
            for i in range(len(services)):
                if services[i].find("nfs") > 0:
                    return True
        except util.CommandException as inst:
            if not int(inst.code) in errlist:
                raise

        util.SMlog("NFS service not ready on server %s" % server)
        retries += 1
        if retries >= NFS_SERVICE_RETRY:
            break

        time.sleep(NFS_SERVICE_WAIT)

    return False


def soft_mount(mountpoint, remoteserver, remotepath, transport, useroptions='',
               timeout=None, nfsversion=DEFAULT_NFSVERSION, retrans=None):
    """Mount the remote NFS export at 'mountpoint'.

    The 'timeout' param here is in deciseconds (tenths of a second). See
    nfs(5) for details.
    """
    try:
        if not util.ioretry(lambda: util.isdir(mountpoint)):
            util.ioretry(lambda: util.makedirs(mountpoint))
    except util.CommandException as inst:
        raise NfsException("Failed to make directory: code is %d" %
                           inst.code)


    # Wait for NFS service to be available
    try:
        if not check_server_service(remoteserver):
            raise util.CommandException(
                code=errno.EOPNOTSUPP,
                reason='No NFS service on server: `%s`' % remoteserver
            )
    except util.CommandException as inst:
        raise NfsException("Failed to detect NFS service on server `%s`"
                           % remoteserver)

    mountcommand = 'mount.nfs'

    options = "soft,proto=%s,vers=%s" % (
        transport,
        nfsversion)
    options += ',acdirmin=0,acdirmax=0'

    if timeout is not None:
        options += ",timeo=%s" % timeout
    if retrans is not None:
        options += ",retrans=%s" % retrans
    if useroptions != '':
        options += ",%s" % useroptions

    try:
        if transport in ['tcp6', 'udp6']:
            remoteserver = '[' + remoteserver + ']'
        util.ioretry(lambda:
                     util.pread([mountcommand, "%s:%s"
                                 % (remoteserver, remotepath),
                                 mountpoint, "-o", options]),
                     errlist=[errno.EPIPE, errno.EIO],
                     maxretry=2, nofail=True)
    except util.CommandException as inst:
        raise NfsException(
            "mount failed on server `%s` with return code %d" % (
                remoteserver, inst.code
            )
        )


def unmount(mountpoint, rmmountpoint):
    """Unmount the mounted mountpoint"""
    try:
        util.pread(["umount", mountpoint])
    except util.CommandException as inst:
        raise NfsException("umount failed with return code %d" % inst.code)

    if rmmountpoint:
        try:
            os.rmdir(mountpoint)
        except OSError as inst:
            raise NfsException("rmdir failed with error '%s'" % inst.strerror)

