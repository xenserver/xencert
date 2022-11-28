#!/bin/bash
#
# Copyright (c) 2005-2022 Citrix Systems Inc.
# Copyright (c) 2022-12-01 Cloud Software Group Holdings, Inc.
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

( echo open ${1}
  sleep 5
  echo ${2}
  sleep 1
  echo ${3}
  sleep 1
  echo admin start
  sleep 1
  echo set port ${4} state ${5}
  sleep 1
  echo admin stop
  sleep 1
  echo quit 
 ) | telnet

