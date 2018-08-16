import commands
import os
import sys
from util import SMlog


logfile = None
logfilename = None


def PrintToLog(message):
    try:
        global logfile
        logfile.write(message)
        logfile.flush()
    except:
        pass

def Print(message):
    # Print to the stdout and to a temp file.
    try:
        sys.stdout.write(message)
        sys.stdout.write('\n')
        global logfile
        logfile.write(message)
        logfile.write('\n')
        logfile.flush()
    except:
        pass

def PrintOnSameLine(message):
    # Print to the stdout and to a temp file.
    try:
        sys.stdout.write(message)
        global logfile
        logfile.write(message)
        logfile.flush()
    except:
        pass

def InitLogging():
    global logfile
    global logfilename
    logfilename = os.path.join('/tmp', 'XenCert-' + commands.getoutput('uuidgen') + '.log')
    logfile = open(logfilename, 'a')

def UnInitLogging():
    global logfile
    logfile.close()

def GetLogFileName():
    global logfilename
    return logfilename

def XenCertPrint(message):
    SMlog("XenCert - " + message)
