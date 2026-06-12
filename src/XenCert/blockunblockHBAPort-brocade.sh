#!/bin/bash
#
# Copyright (c) 2005-2022 Citrix Systems Inc.
# Copyright (c) 2022-2023 Cloud Software Group, Inc.
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

# Log in to the switch over SSH (username on the command line, password via
# sshpass) and run the port enable/disable command. ${5} is portdisable/
# portenable and ${4} is the port number.
sshpass -p "${3}" ssh -o StrictHostKeyChecking=no \
                      -o UserKnownHostsFile=/dev/null \
                      -o HostKeyAlgorithms=+ssh-rsa,rsa-sha2-256,rsa-sha2-512 \
                      -o ConnectTimeout=10 \
                      "${2}@${1}" "${5} ${4}"

