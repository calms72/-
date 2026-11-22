"""Persistent PLC measurement worker. No devices, motion targets or calibration.

Input SHADOW1 envelopes are C-local references; their rxns is NOT camera time.
REJECT1 only rejects malformed source data; malformed IPC terminates the worker.
All fault injection flags exist for offline replay tests, never motor control.
"""
import os
import sys
import time
from visual_protocol import VisualFrameParser
from hand_features import measure_hand

MAX_REQUEST = 2200
NAMES = ('thumb_curl','index_curl','middle_curl','ring_curl','little_curl','thumb_spread_diagnostic')
MODES = ('normal','eof','mismatch','timeout','slow','malformed')

def write_all(data):
    offset = 0
    while offset < len(data):
        count = os.write(1,data[offset:])
        if count <= 0:
            raise OSError('Output closed')
        offset += count

def number(value, positive=False):
    if not value or len(value)>20 or any(x<48 or x>57 for x in value):
        raise ValueError('Invalid reference')
    parsed = int(value)
    if parsed>18446744073709551615 or (positive and parsed==0):
        raise ValueError('Reference out of range')
    return parsed

def decode(line):
    parts = line.split(b'\t')
    if len(parts)!=5 or parts[0]!=b'SHADOW1':
        raise ValueError('Invalid envelope')
    epoch,seq,received = number(parts[1],True),number(parts[2],True),number(parts[3])
    text = parts[4]
    if not text or len(text)>2010 or len(text)%2 or any(x not in b'0123456789abcdefABCDEF' for x in text):
        raise ValueError('Invalid byte encoding')
    return epoch,seq,received,bytes.fromhex(text.decode('ascii'))

def calculate(epoch,seq,received,payload):
    parser = VisualFrameParser()
    frames = parser.feed(payload,received)
    if len(frames)!=1 or parser.buffered_bytes or frames[0]['wire_length']!=len(payload):
        return ('REJECT1\t%d\t%d\t%d\tFRAME_INVALID\n'%(epoch,seq,received)).encode('ascii')
    result = measure_hand(frames[0])
    channels = [result['channels'][name] for name in NAMES]
    if not result['input_valid']:
        return ('REJECT1\t%d\t%d\t%d\tFRAME_INVALID\n'%(epoch,seq,received)).encode('ascii')
    if channels[5]['valid'] or any(c['motor'] is not None or c['predicted_position'] is not None for c in channels):
        raise ValueError('Unexpected motion state')
    mask = sum(1<<i for i,c in enumerate(channels) if c['valid'])
    values = ','.join('NONE' if c['raw'] is None else format(c['raw'],'.9g') for c in channels)
    return ('SHADOW1\t%d\t%d\t%d\t1\t%d\t%s\tNONE,NONE,NONE,NONE,NONE,NONE\n'%(epoch,seq,received,mask,values)).encode('ascii')

def main():
    mode = 'normal'
    if len(sys.argv)!=1:
        if len(sys.argv)!=3 or sys.argv[1]!='--test-case' or sys.argv[2] not in MODES:
            return 2
        mode = sys.argv[2]
    write_all(b'READY\n')
    pending = bytearray()
    epoch_seen = None
    last_seq = 0
    last_rx = 0
    while True:
        newline = pending.find(b'\n')
        if newline < 0:
            if len(pending)>=MAX_REQUEST:
                return 3
            chunk = os.read(0,MAX_REQUEST-len(pending))
            if not chunk:
                return 3 if pending else 0
            pending.extend(chunk)
            continue
        line = bytes(pending[:newline])
        del pending[:newline+1]
        epoch,seq,received,payload = decode(line)
        if (epoch_seen is not None and epoch!=epoch_seen) or seq<=last_seq or received<last_rx:
            return 3
        epoch_seen,last_seq,last_rx = epoch,seq,received
        if mode=='eof':
            return 0
        if mode=='timeout':
            time.sleep(3.0)
        elif mode=='slow':
            time.sleep(0.06)
        elif mode=='malformed':
            write_all(b'BROKEN\n')
            continue
        reply_seq = seq+1 if mode=='mismatch' else seq
        write_all(calculate(epoch,reply_seq,received,payload))

if __name__=='__main__':
    try:
        sys.exit(main())
    except (ValueError,OverflowError,OSError):
        sys.exit(3)
