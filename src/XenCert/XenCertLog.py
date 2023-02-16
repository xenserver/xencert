import subprocess
import os
import sys
from util import SMlog


logfile = None
logfilename = None


def print_to_log(message):
    try:
        global logfile
        logfile.write(message)
        logfile.flush()
    except:
        pass

def printout(message):
    # printout to the stdout and to a temp file.
    try:
        sys.stdout.write(message)
        sys.stdout.write('\n')
        global logfile
        logfile.write(message)
        logfile.write('\n')
        logfile.flush()
    except:
        pass

def print_on_same_line(message):
    # printout to the stdout and to a temp file.
    try:
        sys.stdout.write(message)
        global logfile
        logfile.write(message)
        logfile.flush()
    except:
        pass

def init_logging():
    global logfile
    global logfilename
    logfilename = os.path.join('/tmp', 'XenCert-' + subprocess.getoutput('uuidgen') + '.log')    # NOSONAR
    logfile = open(logfilename, 'a')

def uninit_logging():
    global logfile
    logfile.close()

def get_log_file_name():
    global logfilename
    return logfilename

def xencert_print(message):
    SMlog("XenCert - " + message)
