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

import util
import xs_errors
import os


DMPBIN = "/sbin/multipath"
DEVMAPPERPATH = "/dev/mapper"
DEVBYIDPATH = "/dev/disk/by-id"
DEVBYSCSIPATH = "/dev/disk/by-scsibus"
DEVBYMPPPATH = "/dev/disk/by-mpp"
SYSFS_PATH = '/sys/class/scsi_host'
MP_INUSEDIR = "/dev/disk/mpInuse"

MPPGETAIDLNOBIN = "/opt/xensource/bin/xe-get-arrayid-lunnum"


def _is_mpath_daemon_running():
    cmd = ["/sbin/pidof", "-s", "/sbin/multipathd"]
    (rc, stdout, stderr) = util.doexec(cmd)
    return (rc == 0)


def _is_valid_multipath_device(sid):

    # Check if device is already multipathed
    (ret, stdout, stderr) = util.doexec(['/usr/sbin/multipath', '-l', sid])
    if not stdout + stderr:
        (ret, stdout, stderr) = util.doexec(['/usr/sbin/multipath', '-ll', sid])
    if not stdout + stderr:
        (ret, stdout, stderr) = util.doexec(['/usr/sbin/multipath', '-a', sid])
        if ret < 0:
            util.SMlog("Failed to add {}: wwid could be explicitly "
                       "blacklisted\n Continue with multipath disabled for "
                       "this SR".format(sid))
            return False

        by_scsid_path = "/dev/disk/by-scsid/" + sid
        if os.path.exists(by_scsid_path):
            devs = os.listdir(by_scsid_path)
        else:
            util.SMlog("Device {} is not ready yet, skipping multipath check"
                       .format(by_scsid_path))
            return False
        ret = 1
        # Some paths might be down, check all associated devices
        for dev in devs:
            devpath = os.path.join(by_scsid_path, dev)
            real_path = util.get_real_path(devpath)
            (ret, stdout, stderr) = util.doexec(['/usr/sbin/multipath', '-c',
                                                 real_path])
            if ret == 0:
                break

        if ret == 1:
            # This is very fragile but it is not a good sign to fail without
            # any output. At least until multipath 0.4.9, for example,
            # multipath -c fails without any log if it is able to retrieve the
            # wwid of the device.
            # In this case it is better to fail immediately.
            if not stdout + stderr:
                # Attempt to cleanup wwids file before raising
                try:
                    (ret, stdout, stderr) = util.doexec(['/usr/sbin/multipath',
                                                         '-w', sid])
                except OSError:
                    util.SMlog("Error removing {} from wwids file".format(sid))
                raise xs_errors.XenError('MultipathGenericFailure',
                                         '"multipath -c" failed without any'
                                         ' output on {}'.format(real_path))
            util.SMlog("When dealing with {} multipath status returned:\n "
                       "{}{} Continue with multipath disabled for this SR"
                       .format(sid, stdout, stderr))
            return False
    return True


def path(SCSIid):
    if _is_valid_multipath_device(SCSIid) and _is_mpath_daemon_running():
        path = os.path.join(MP_INUSEDIR, SCSIid)
        return path
    else:
        return DEVBYIDPATH + "/scsi-" + SCSIid
