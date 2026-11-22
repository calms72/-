"""PLC-local offline feature preview from saved samples. No hardware I/O."""
import os
import sys
import time
from visual_protocol import VisualFrameParser
from hand_features import measure_hand

def main():
    if (len(sys.argv)-1)%2:
        raise ValueError('Expected fixture-path and frame-count pairs')
    total = 0
    feature_ns = 0
    start = time.monotonic_ns()
    names = ('thumb_curl','index_curl','middle_curl','ring_curl','little_curl','thumb_spread_diagnostic')
    print('MODE\tboard_offline_measurement_preview_no_targets',flush=True)
    for index in range(1,len(sys.argv),2):
        path,expected = sys.argv[index],int(sys.argv[index+1])
        stat = os.stat(path)
        if (stat.st_mode & 0o170000)!=0o100000 or stat.st_size>2_000_000:
            raise ValueError('Bounded regular fixtures required')
        parser = VisualFrameParser()
        count = 0
        valid = dict((name,0) for name in names)
        with open(path,'rb') as stream:
            while True:
                block = stream.read(512)
                if not block:
                    break
                frames = parser.feed(block,time.monotonic_ns())
                for frame in frames:
                    before = time.monotonic_ns()
                    result = measure_hand(frame)
                    feature_ns += time.monotonic_ns()-before
                    if not result['input_valid']:
                        raise AssertionError('Parser and feature input disagree')
                    for name,channel in result['channels'].items():
                        if channel['motor'] is not None or channel['predicted_position'] is not None:
                            raise AssertionError('Motion target in preview')
                        if channel['valid']:
                            valid[name] += 1
                    if result['channels']['thumb_spread_diagnostic']['valid']:
                        raise AssertionError('Uncalibrated side channel became valid')
                    count += 1
        if count!=expected or parser.buffered_bytes:
            raise AssertionError('Fixture count/partial frame mismatch')
        total += count
        print('FIXTURE\t'+os.path.basename(path)+'\t'+str(count),flush=True)
        for name in names:
            print('VALID\t'+os.path.basename(path)+'\t'+name+'\t'+str(valid[name])+'\t'+str(count),flush=True)
    print('TOTAL\t'+str(total)+'\t'+str(feature_ns)+'\t'+str(time.monotonic_ns()-start),flush=True)
    print('MOTION_TARGETS\tNONE',flush=True)

if __name__=='__main__':
    main()
