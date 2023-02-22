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
# Miscellaneous utility functions
#

import os
import re
import sys
import subprocess
import signal
import time
import errno
import stat
import xs_errors
import XenAPI
import syslog
import traceback
import glob


from functools import reduce

NO_LOGGING_STAMPFILE = '/etc/xensource/no_sm_log'

IORETRY_MAX = 20  # retries
IORETRY_PERIOD = 1.0  # seconds

LOGGING = not (os.path.exists(NO_LOGGING_STAMPFILE))
_SM_SYSLOG_FACILITY = syslog.LOG_LOCAL2
LOG_EMERG = syslog.LOG_EMERG
LOG_ALERT = syslog.LOG_ALERT
LOG_CRIT = syslog.LOG_CRIT
LOG_ERR = syslog.LOG_ERR
LOG_WARNING = syslog.LOG_WARNING
LOG_NOTICE = syslog.LOG_NOTICE
LOG_INFO = syslog.LOG_INFO
LOG_DEBUG = syslog.LOG_DEBUG

ISCSI_REFDIR = '/var/run/sr-ref'

CMD_DD = "/bin/dd"

FIST_PAUSE_PERIOD = 30  # seconds


class SMException(Exception):
    """Base class for all SM exceptions for easier catching & wrapping in 
    XenError"""
    pass


class CommandException(SMException):
    def error_message(self, code):
        if code > 0:
            return os.strerror(code)
        elif code < 0:
            return "Signalled %s" % (abs(code))
        return "Success"

    def __init__(self, code, cmd="", reason='exec failed'):
        self.code = code
        self.cmd = cmd
        self.reason = reason
        Exception.__init__(self, self.error_message(code))


def logException(tag):
    info = sys.exc_info()
    if info[0] == SystemExit:
        # this should not be happening when catching "Exception", but it is
        sys.exit(0)
    tb = reduce(lambda a, b: "%s%s" % (a, b), traceback.format_tb(info[2]))
    str = "***** %s: EXCEPTION %s, %s\n%s" % (tag, info[0], info[1], tb)
    SMlog(str)


def roundup(divisor, value):
    """Retruns the rounded up value so it is divisible by divisor."""

    if value == 0:
        value = 1
    if value % divisor != 0:
        return ((int(value) // divisor) + 1) * divisor
    return value


def to_plain_string(obj):
    if obj is None:
        return None
    if type(obj) == str:
        return obj
    return str(obj)


def _logToSyslog(ident, facility, priority, message):
    syslog.openlog(ident, 0, facility)
    syslog.syslog(priority, "[%d] %s" % (os.getpid(), message))
    syslog.closelog()


def SMlog(message, ident="SM", priority=LOG_INFO):
    if LOGGING:
        for message_line in str(message).split('\n'):
            _logToSyslog(ident, _SM_SYSLOG_FACILITY, priority, message_line)


def doexec(args, inputtext=None, new_env=None, text=True):
    """Execute a subprocess, then return its return code, stdout and stderr"""
    env = None
    if new_env:
        env = dict(os.environ)
        env.update(new_env)
    proc = subprocess.Popen(args, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            close_fds=True, env=env,
                            universal_newlines=text)

    if not text and inputtext is not None:
        inputtext = inputtext.encode()

    (stdout, stderr) = proc.communicate(inputtext)

    rc = proc.returncode
    return rc, stdout, stderr


def is_string(value):
    return isinstance(value, str)


# These are partially tested functions that replicate the behaviour of
# the original pread,pread2 and pread3 functions. Potentially these can
# replace the original ones at some later date.
#
# cmdlist is a list of either single strings or pairs of strings. For
# each pair, the first component is passed to exec while the second is
# written to the logs.
def pread(cmdlist, close_stdin=False, scramble=None, expect_rc=0,
          quiet=False, new_env=None, text=True):
    cmdlist_for_exec = []
    cmdlist_for_log = []
    for item in cmdlist:
        if is_string(item):
            cmdlist_for_exec.append(item)
            if scramble:
                if item.find(scramble) != -1:
                    cmdlist_for_log.append("<filtered out>")
                else:
                    cmdlist_for_log.append(item)
            else:
                cmdlist_for_log.append(item)
        else:
            cmdlist_for_exec.append(item[0])
            cmdlist_for_log.append(item[1])

    if not quiet:
        SMlog(cmdlist_for_log)
    (rc, stdout, stderr) = doexec(cmdlist_for_exec, new_env=new_env, text=text)
    if rc != expect_rc:
        SMlog("FAILED in util.pread: (rc %d) stdout: '%s', stderr: '%s'" % \
                (rc, stdout, stderr))
        if quiet:
            SMlog("Command was: %s" % cmdlist_for_log)
        if '' == stderr:
            stderr = stdout
        raise CommandException(rc, str(cmdlist), stderr.strip())
    if not quiet:
        SMlog("  pread SUCCESS")
    return stdout


#Read STDOUT from cmdlist and discard STDERR output
def pread2(cmdlist, quiet=False, text=True):
    return pread(cmdlist, quiet=quiet, text=text)


#Read STDOUT from cmdlist, feeding 'text' to STDIN
def pread3(cmdlist, text):
    SMlog(cmdlist)
    (rc, stdout, stderr) = doexec(cmdlist, text)
    if rc:
        SMlog("FAILED in util.pread3: (errno %d) stdout: '%s', stderr: '%s'" % \
                (rc, stdout, stderr))
        if '' == stderr:
            stderr = stdout
        raise CommandException(rc, str(cmdlist), stderr.strip())
    SMlog("  pread3 SUCCESS")
    return stdout


def listdir(path, quiet=False):
    cmd = ["ls", path, "-1", "--color=never"]
    try:
        text = pread2(cmd, quiet=quiet)[:-1]
        if len(text) == 0:
            return []
        return text.split('\n')
    except CommandException as inst:
        if inst.code == errno.ENOENT:
            raise CommandException(errno.EIO, inst.cmd, inst.reason)
        else:
            raise CommandException(inst.code, inst.cmd, inst.reason)


def gen_uuid():
    cmd = ["uuidgen", "-r"]
    return pread(cmd)[:-1]


def match_uuid(s):
    regex = re.compile("^[0-9a-f]{8}-(([0-9a-f]{4})-){3}[0-9a-f]{12}")
    return regex.search(s, 0)


def findall_uuid(s):
    regex = re.compile("[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
    return regex.findall(s, 0)


def ioretry(f, errlist=[errno.EIO], maxretry=IORETRY_MAX, period=IORETRY_PERIOD, **ignored):
    retries = 0
    while True:
        try:
            return f()
        except OSError as ose:
            err = int(ose.errno)
            if not err in errlist:
                raise CommandException(err, str(f), "OSError")
        except CommandException as ce:
            if not int(ce.code) in errlist:
                raise

        retries += 1
        if retries >= maxretry:
            break

        time.sleep(period)

    raise CommandException(errno.ETIMEDOUT, str(f), "Timeout")


def ioretry_stat(path, maxretry=IORETRY_MAX):
    # this ioretry is similar to the previous method, but
    # stat does not raise an error -- so check its return
    retries = 0
    while retries < maxretry:
        stat = os.statvfs(path)
        if stat.f_blocks != -1:
            return stat
        time.sleep(1)
        retries += 1
    raise CommandException(errno.EIO, "os.statvfs")


def pathexists(path):
    try:
        os.lstat(path)
        return True
    except OSError as inst:
        if inst.errno == errno.EIO:
            time.sleep(1)
            try:
                listdir(os.path.realpath(os.path.dirname(path)))
                os.lstat(path)
                return True
            except:
                pass
            raise CommandException(errno.EIO, "os.lstat(%s)" % path, "failed")
        return False


def get_real_path(path):
    "Follow symlinks to the actual file"
    absPath = path
    directory = ''
    while os.path.islink(absPath):
        directory = os.path.dirname(absPath)
        absPath = os.readlink(absPath)
        absPath = os.path.join(directory, absPath)
    return absPath


def wait_for_path(path, timeout):
    for i in range(0, timeout):
        if len(glob.glob(path)):
            return True
        time.sleep(1)
    return False


def wait_for_nopath(path, timeout):
    for i in range(0, timeout):
        if not os.path.exists(path):
            return True
        time.sleep(1)
    return False


def wait_for_path_multi(path, timeout):
    for i in range(0, timeout):
        paths = glob.glob(path)
        SMlog("_wait_for_paths_multi: paths = %s" % paths)
        if len(paths):
            SMlog("_wait_for_paths_multi: return first path: %s" % paths[0])
            return paths[0]
        time.sleep(1)
    return ""


def isdir(path):
    try:
        st = os.stat(path)
        return stat.S_ISDIR(st.st_mode)
    except OSError as inst:
        if inst.errno == errno.EIO:
            raise CommandException(errno.EIO, "os.stat(%s)" % path, "failed")
        return False


def get_single_entry(path):
    f = open(path, 'r')
    line = f.readline()
    f.close()
    return line.rstrip()


def get_fs_size(path):
    st = ioretry_stat(path)
    return st.f_blocks * st.f_frsize


def get_fs_utilisation(path):
    st = ioretry_stat(path)
    return (st.f_blocks - st.f_bfree) * \
            st.f_frsize


def makedirs(name, mode=0o777):
    head, tail = os.path.split(name)
    if not tail:
        head, tail = os.path.split(head)
    if head and tail and not pathexists(head):
        makedirs(head, mode)
        if tail == os.curdir:
            return
    try:
        os.mkdir(name, mode)
    except OSError as exc:
        if exc.errno == errno.EEXIST and os.path.isdir(name):
            if mode:
                os.chmod(name, mode)
            pass
        else:
            raise


def zeroOut(path, fromByte, bytes):
    """write 'bytes' zeros to 'path' starting from fromByte (inclusive)"""
    blockSize = 4096

    fromBlock = fromByte // blockSize
    if fromByte % blockSize:
        fromBlock += 1
        bytesBefore = fromBlock * blockSize - fromByte
        if bytesBefore > bytes:
            bytesBefore = bytes
        bytes -= bytesBefore
        cmd = [CMD_DD, "if=/dev/zero", "of=%s" % path, "bs=1",
               "seek=%s" % fromByte, "count=%s" % bytesBefore]
        try:
            pread2(cmd)
        except CommandException:
            return False

    blocks = bytes // blockSize
    bytes -= blocks * blockSize
    fromByte = (fromBlock + blocks) * blockSize
    if blocks:
        cmd = [CMD_DD, "if=/dev/zero", "of=%s" % path, "bs=%s" % blockSize,
               "seek=%s" % fromBlock, "count=%s" % blocks]
        try:
            pread2(cmd)
        except CommandException:
            return False

    if bytes:
        cmd = [CMD_DD, "if=/dev/zero", "of=%s" % path, "bs=1",
               "seek=%s" % fromByte, "count=%s" % bytes]
        try:
            pread2(cmd)
        except CommandException:
            return False

    return True


def match_rootdev(s):
    regex = re.compile("^PRIMARY_DISK")
    return regex.search(s, 0)


def getrootdev():
    filename = '/etc/xensource-inventory'
    try:
        f = open(filename, 'r')
    except:
        raise xs_errors.XenError('EIO', \
              opterr="Unable to open inventory file [%s]" % filename)
    rootdev = ''
    for line in filter(match_rootdev, f.readlines()):
        rootdev = line.split("'")[1]
    if not rootdev:
        raise xs_errors.XenError('NoRootDev')
    return rootdev


def get_localAPI_session():
    # First acquire a valid session
    session = XenAPI.xapi_local()
    try:
        session.xenapi.login_with_password('root', '', '', 'SM')
    except:
        raise xs_errors.XenError('APISession')
    return session


def get_this_host():
    uuid = None
    f = open("/etc/xensource-inventory", 'r')
    for line in f.readlines():
        if line.startswith("INSTALLATION_UUID"):
            uuid = line.split("'")[1]
    f.close()
    return uuid


def get_localhost_ref(session):
    filename = '/etc/xensource-inventory'
    try:
        f = open(filename, 'r')
    except:
        raise xs_errors.XenError('EIO', \
              opterr="Unable to open inventory file [%s]" % filename)
    domid = ''
    for line in filter(match_domain_id, f.readlines()):
        domid = line.split("'")[1]
    if not domid:
        raise xs_errors.XenError('APILocalhost')

    vms = session.xenapi.VM.get_all_records_where('field "uuid" = "%s"' % domid)
    for vm in vms:
        record = vms[vm]
        if record["uuid"] == domid:
            hostid = record["resident_on"]
            return hostid
    raise xs_errors.XenError('APILocalhost')


def match_domain_id(s):
    regex = re.compile("^CONTROL_DOMAIN_UUID")
    return regex.search(s, 0)


def get_this_host_ref(session):
    host_uuid = get_this_host()
    host_ref = session.xenapi.host.get_by_uuid(host_uuid)
    return host_ref


def find_my_pbd_record(session, host_ref, sr_ref):
    try:
        pbds = session.xenapi.PBD.get_all_records()
        for pbd_ref in pbds.keys():
            if pbds[pbd_ref]['host'] == host_ref and pbds[pbd_ref]['SR'] == sr_ref:
                return [pbd_ref, pbds[pbd_ref]]
        return None
    except Exception as e:
        SMlog("Caught exception while looking up PBD for host %s SR %s: %s" % (str(host_ref), str(sr_ref), str(e)))
        return None


def find_my_pbd(session, host_ref, sr_ref):
    ret = find_my_pbd_record(session, host_ref, sr_ref)
    if ret is not None:
        return ret[0]
    else:
        return None


class TimeoutException(SMException):
    pass


def timeout_call(timeoutseconds, function, *arguments):
    def handler(signum, frame):
        raise TimeoutException()
    signal.signal(signal.SIGALRM, handler)
    signal.alarm(timeoutseconds)
    try:
        return function(*arguments)
    finally:
        signal.alarm(0)


# Given a partition (e.g. sda1), get a disk name:
def diskFromPartition(partition):
    # check whether this is a device mapper device (e.g. /dev/dm-0)
    m = re.match('(/dev/)?(dm-[0-9]+)(p[0-9]+)?$', partition)
    if m is not None:
        return m.group(2)

    numlen = 0  # number of digit characters
    m = re.match("\D+(\d+)", partition)
    if m is not None:
        numlen = len(m.group(1))

    # is it a cciss?
    if True in [partition.startswith(x) for x in ['cciss', 'ida', 'rd']]:
        numlen += 1  # need to get rid of trailing 'p'

    # is it a mapper path?
    if partition.startswith("mapper"):
        if re.search("p[0-9]*$", partition):
            numlen = len(re.match("\d+", partition[::-1]).group(0)) + 1
            SMlog("Found mapper part, len %d" % numlen)
        else:
            numlen = 0

    # is it /dev/disk/by-id/XYZ-part<k>?
    if partition.startswith("disk/by-id"):
        return partition[:partition.rfind("-part")]

    return partition[:len(partition) - numlen]


def dom0_disks():
    """Disks carrying dom0, e.g. ['/dev/sda']"""
    disks = []
    with open("/etc/mtab", 'r') as f:
        for line in f:
            (dev, mountpoint, fstype, opts, freq, passno) = line.split(' ')
            if mountpoint == '/':
                disk = diskFromPartition(dev)
                if not (disk in disks):
                    disks.append(disk)
    SMlog("Dom0 disks: %s" % disks)
    return disks


if __debug__:
    try:
        XE_IOFI_IORETRY
    except NameError:
        XE_IOFI_IORETRY = os.environ.get('XE_IOFI_IORETRY', None)
    if __name__ == 'util' and XE_IOFI_IORETRY is not None:
        __import__('iofi')


def unictrunc(string, max_bytes):
    """
    Returns the number of bytes that is smaller than, or equal to, the number
    of bytes specified, such that the UTF-8 encoded string can be correctly
    truncated.
    string: the string to truncate
    max_bytes: the maximum number of bytes the truncated string can be
    """
    string = string.decode('UTF-8')
    cur_bytes = 0
    for char in string:
        charsize = len(char.encode('UTF-8'))
        if cur_bytes + charsize > max_bytes:
            break
        else:
            cur_bytes = cur_bytes + charsize
    return cur_bytes










