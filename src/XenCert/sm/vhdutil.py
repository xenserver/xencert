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
# Helper functions pertaining to VHD operations
#

import util


MIN_VHD_SIZE = 2 * 1024 * 1024
MAX_VHD_SIZE = 2040 * 1024 * 1024 * 1024
MAX_VHD_JOURNAL_SIZE = 6 * 1024 * 1024  # 2MB VHD block size, max 2TB VHD size
MAX_CHAIN_SIZE = 30  # max VHD parent chain size
VHD_UTIL = "/usr/bin/vhd-util"
OPT_LOG_ERR = "--debug"
VHD_BLOCK_SIZE = 2 * 1024 * 1024
VHD_FOOTER_SIZE = 512

# lock to lock the entire SR for short ops
LOCK_TYPE_SR = "sr"

VDI_TYPE_VHD = 'vhd'
VDI_TYPE_RAW = 'aio'

FILE_EXTN_VHD = ".vhd"
FILE_EXTN_RAW = ".raw"
FILE_EXTN = {
        VDI_TYPE_VHD: FILE_EXTN_VHD,
        VDI_TYPE_RAW: FILE_EXTN_RAW
}


class VHDInfo:
    uuid = ""
    path = ""
    sizeVirt = -1
    sizePhys = -1
    hidden = False
    parentUuid = ""
    parentPath = ""
    error = 0

    def __init__(self, uuid):
        self.uuid = uuid


def calcOverheadEmpty(virtual_size):
    """Calculate the VHD space overhead (metadata size) for an empty VDI of
    size virtual_size"""
    overhead = 0
    size_mb = virtual_size // (1024 * 1024)

    # Footer + footer copy + header + possible CoW parent locator fields
    overhead = 3 * 1024

    # BAT 4 Bytes per block segment
    overhead += (size_mb // 2) * 4
    overhead = util.roundup(512, overhead)

    # BATMAP 1 bit per block segment
    overhead += (size_mb // 2) // 8
    overhead = util.roundup(4096, overhead)

    return overhead
