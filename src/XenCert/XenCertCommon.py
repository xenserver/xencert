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

"""Manual Xen Certification script"""
import time
from optparse import OptionParser
from XenCertLog import print_to_log, printout


storage_type = "storage type (iscsi, hba, nfs, isl, fcoe)"
HIDDEN_PASSWORD = '*' * 8

TAG_PASS = "[PASS]"
TAG_FAIL = "[FAIL]"

# argument format:
#  keyword
#  text
#  white space
#  default value
#  short form of option
#  log form of option
__nfs_args__ = [
    ["server",          "server name/IP addr", " : ", None,        "required", "-n", ""   ],
    ["serverpath",      "exported path", " : ", None,        "required", "-e", ""     ] ]

__cifs_args__ = [
    ["server",   "Full path to share root on CIFS server",             " : ", None, "required", "-r", "" ],
    ["username", "The username to be used during CIFS authentication", " : ", None, "required", "-y", "" ],
    ["password", "The password to be used during CIFS authentication", " : ", None, "required", "-p", "" ] ]

__hba_args__ = [
    ["adapters",       "comma separated list of HBAs to test against", " : ", None,        "optional", "-a", ""   ],
    ["scsiIDs",       "comma separated list of SCSI-IDs to test against", " : ", None,        "required", "-S", ""   ] ]

__isl_args__ = [
    ["file",       "configuration file describing target array paramters", " : ", None,        "required", "-F", ""   ] ]

__iscsi_args__ = [
    ["target",          "comma separated list of Target names/IP addresses", " : ", None,        "required", "-t", ""      ],
    ["targetIQN",       "comma separated list of target IQNs OR \"*\"", " : ", None,        "required", "-q", ""      ],
    ["SCSIid",        "SCSIid to use for SR creation",                  " : ", '',          "optional", "-s", ""    ],
    ["chapuser",        "username for CHAP", " : ", '',        "optional", "-x", ""    ],
    ["chappasswd",      "password for CHAP", " : ", '',        "optional", "-w", ""  ] ]


__common__ = [    
    ["functional", "perform functional tests",                          " : ", None, "optional", "-f", ""],
    ["control", "perform control path tests",                           " : ", None, "optional", "-c", ""],
    ["multipath", "perform multipath configuration verification tests", " : ", None, "optional", "-m", ""],
    ["pool", "perform pool verification tests",                         " : ", None, "optional", "-o", ""],
    ["data", "perform data verification tests",                         " : ", None, "optional", "-d", ""],
    ["metadata", "perform metadata tests",                              " : ", None, "optional", "-M", ""],
    ["help",    "show this help message and exit",                                  " : ", None,        "optional", "-h", "" ]]

__commonparams__ = [
    ["storage_type",    storage_type,                     " : ", None, "required", "-b", ""],
    ["pathHandlerUtil", "absolute path to admin provided callout utility which blocks/unblocks a list of paths, path related information should be provided with the -i option below",
                                                                                    " : ", None, "optional", "-u", ""],
    ["pathInfo", "pass-through string used to pass data to the callout utility above, for e.g. login credentials etc. This string is passed as-is to the callout utility. ",
                                                                                    " : ", None, "optional", "-i", ""],
    ["count", "count of iterations to perform in case of multipathing failover testing",
                                                                                    " : ", None, "optional", "-g", ""]]

def parse_args(version_string):
    """Parses the command line arguments"""
    
    opt = OptionParser("usage: %prog [arguments seen below]", version="%prog " + version_string, add_help_option=False)    # NOSONAR
    
    for element in __nfs_args__:
        opt.add_option(element[5], element[6],
                       default=element[3],
                       help=element[1],
                       dest=element[0])

    for element in __cifs_args__:
        opt.add_option(element[5], element[6],
                       default=element[3],
                       help=element[1],
                       dest=element[0])
    
    for element in __hba_args__:
        opt.add_option(element[5], element[6],
                       default=element[3],
                       help=element[1],
                       dest=element[0])
   
    for element in __isl_args__:
        opt.add_option(element[5], element[6],
                       default=element[3],
                       help=element[1],
                       dest=element[0])

    for element in __iscsi_args__:
        opt.add_option(element[5], element[6],
                       default=element[3],
                       help=element[1],
                       dest=element[0])

    for element in __commonparams__:
        opt.add_option(element[5], element[6],
                       default=element[3],
                       help=element[1],
                       dest=element[0])
    
    for element in __common__:
        opt.add_option(element[5], element[6],
                       action="store_true",
                       default=element[3],
                       help=element[1],
                       dest=element[0])

    return opt.parse_args()

def store_configuration(g_storage_conf, options):
    """Stores the command line arguments in a class"""

    g_storage_conf["storage_type"] = options.storage_type
    try:
        g_storage_conf["slavehostname"] = options.slavehostname
    except:
        pass

def valid_arguments(options, g_storage_conf):
    """ validate arguments """
    if not options.storage_type in ["hba", "nfs", "cifs", "iscsi", "isl", "fcoe"]:
        printout("Error: storage type (hba, nfs, cifs, isl, fcoe or iscsi) is required")
        return 0

    for element in __commonparams__:
        if not getattr(options, element[0]):
            if element[4] == "required":
                printout("Error: %s argument (%s: %s) for storage type %s" \
                       % (element[4], element[5], element[1], options.storage_type))
                return 0
            else:
                g_storage_conf[element[0]] = "" 
        value = getattr(options, element[0])
        g_storage_conf[element[0]] = value

    if options.storage_type == "nfs":
        subargs = __nfs_args__
    elif options.storage_type == "cifs":
        subargs = __cifs_args__
    elif options.storage_type in ["hba", "fcoe"]:
        subargs = __hba_args__
    elif options.storage_type == "isl":
        subargs = __isl_args__
    elif options.storage_type == "iscsi":
        subargs = __iscsi_args__

    for element in subargs:
        if not getattr(options, element[0]):
            if element[4] == "required":
                printout("Error: %s argument (%s: %s) for storage type %s" \
                       % (element[4], element[5], element[1], options.storage_type))
                display_usage(options.storage_type)
                return 0
            else:
                g_storage_conf[element[0]] = "" 
        value = getattr(options, element[0])
        g_storage_conf[element[0]] = value
        
    return 1

def display_common_options():
    printout("usage: XenCert [arguments seen below] \n\
\n\
Common options:\n")
    for item in __common__:
        print_help_item(item)
    
def display_iscsi_options():
    printout(" Storage type iscsi:\n")
    for item in __iscsi_args__:
        print_help_item(item)
 
def display_nfs_options():
    printout(" Storage type nfs:\n")
    for item in __nfs_args__:
        print_help_item(item)

def display_cifs_options():
    printout(" Storage type cifs:\n")
    for item in __cifs_args__:
        print_help_item(item)
  
def display_hba_options():
    printout(" Storage type hba:\n")
    for item in __hba_args__:
        print_help_item(item)    

def display_isl_options():
    printout(" Storage type isl:\n")
    for item in __isl_args__:
        print_help_item(item)    
  
def display_test_specific_options():
    printout("Test specific options:")
    printout("Multipathing test options (-m above):\n")
    for item in __commonparams__:
        print_help_item(item)

def display_storage_specific_usage(storage_type):
    if storage_type == 'iscsi':
        display_iscsi_options()
    elif storage_type == 'nfs':
        display_nfs_options()
    elif storage_type == 'cifs':
        display_cifs_options()
    elif storage_type in ['hba', 'fcoe']:
        display_hba_options()
    elif storage_type == 'isl':
        display_isl_options()
    elif storage_type is None:
        display_iscsi_options()
        printout("")
        display_nfs_options()
        printout("")
        display_cifs_options()
        printout("")
        display_hba_options()        
        printout("")
        display_isl_options()        
     
def display_usage(storage_type=None):
    display_common_options()
    printout("\nStorage specific options:\n")
    display_storage_specific_usage(storage_type)
    printout("")
    display_test_specific_options()

def print_help_item(item):
    printout(" %s %-20s\t[%s] %s" % (item[5], item[0], item[4], item[1]))
    
def print_command(argvs):
    temp_argvs = argvs[:]
    for option in ['-i', '-w', '-p']:
        try:
            option_index = temp_argvs.index(option)
        except ValueError, e:
            pass
        else:
            if option == '-i':
                temp_argvs[option_index+1] = ':'.join(get_cmds_with_hidden_password(temp_argvs[option_index + 1].split(':'), 2))
            else:
                temp_argvs[option_index+1] = HIDDEN_PASSWORD
    for argv in temp_argvs:
        print_to_log(argv)
        print_to_log(' ')

def display_operation_status(pass_or_fail, custom_value=''):
    if pass_or_fail:
        printout("                                                                                                   PASS [Completed%s]" % custom_value)
    else:
        printout("                                                                                                   FAIL [%s]" % time.asctime(time.localtime()))

def get_cmds_with_hidden_password(cmd, password_index=-3):
    cmd_with_hidden_password = cmd[:]
    cmd_with_hidden_password[password_index] = HIDDEN_PASSWORD
    return cmd_with_hidden_password

def get_config_with_hidden_password(config, storage_type):
    config_with_hidden_password = dict(config)
    if storage_type == 'iscsi' and config.get('chappassword') is not None:
        config_with_hidden_password['chappassword'] = HIDDEN_PASSWORD
    elif storage_type == 'cifs' and config.get('password') is not None:
        config_with_hidden_password['password'] = HIDDEN_PASSWORD
    else:
        pass
    return config_with_hidden_password

def hide_path_info_password(path_info, delimiter=':', password_index=2):
    info_list = path_info.split(delimiter)
    if len(info_list) > password_index:
        info_list[password_index] = HIDDEN_PASSWORD
    return delimiter.join(info_list)

def show_report(msg, result, checkpoints=1, total_checkpoints=1, time=0):
    printout("%-50s: %s, Pass percentage: %d, Completed: %s" %
          (msg, TAG_PASS if result else TAG_FAIL, int((checkpoints * 100) / total_checkpoints), time))
