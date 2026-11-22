"""Replay saved raw files on PLC. No serial, CAN, service or configuration I/O."""
import os
import sys
import time
from visual_protocol import VisualFrameParser

def percentile(values, fraction):
    values = sorted(values)
    if not values:
        return 0
    position = (len(values)-1)*fraction
    index = int(position)
    return values[index]+(values[min(index+1,len(values)-1)]-values[index])*(position-index)

def main():
    if (len(sys.argv)-1)%2 or len(sys.argv)<3:
        raise ValueError('Arguments must be fixture-path expected-frame-count pairs')
    total_frames = 0
    total_bytes = 0
    durations = []
    print('MODE\tboard_offline_replay_no_hardware',flush=True)
    print('PYTHON\t'+sys.version.split()[0],flush=True)
    all_start = time.monotonic_ns()
    for i in range(1,len(sys.argv),2):
        path, expected = sys.argv[i], int(sys.argv[i+1])
        stat = os.stat(path)
        if (stat.st_mode & 0o170000)!=0o100000 or stat.st_size>2_000_000:
            raise ValueError('Fixture must be a bounded regular file')
        parser = VisualFrameParser()
        count = 0
        start = time.monotonic_ns()
        with open(path,'rb') as stream:
            while True:
                block = stream.read(4096)
                if not block:
                    break
                before = time.monotonic_ns()
                frames = parser.feed(block,before)
                durations.append(time.monotonic_ns()-before)
                for frame in frames:
                    if len(frame['values'])!=63 or type(frame['values']) is not tuple:
                        raise AssertionError('Publication mismatch')
                count += len(frames)
        elapsed = time.monotonic_ns()-start
        counters = parser.counters
        if count!=expected or parser.buffered_bytes:
            raise AssertionError('Count/partial-frame mismatch: '+path)
        for key,value in counters.items():
            if key not in ('input_bytes','frames','buffer_peak_bytes') and value:
                raise AssertionError('Unexpected parser counter: '+key+'='+str(value))
        if counters['buffer_peak_bytes']>parser.buffer_capacity:
            raise AssertionError('Buffer capacity exceeded')
        total_frames += count
        total_bytes += stat.st_size
        print('FIXTURE\t'+os.path.basename(path)+'\t'+str(stat.st_size)+'\t'+str(count)+'\t'+str(expected)+'\t'+str(elapsed)+'\t'+str(counters['buffer_peak_bytes']),flush=True)
    # A stalled partial frame must be discarded without publishing any values.
    parser = VisualFrameParser(frame_timeout_ns=100_000_000)
    if parser.feed(b'\r335,0.',0) or not parser.expire(100_000_000) or parser.buffered_bytes:
        raise AssertionError('Absolute frame timeout failed on board')
    print('TIMEOUT_TEST\tPASS',flush=True)
    print('TOTAL\t'+str(total_bytes)+'\t'+str(total_frames)+'\t'+str(time.monotonic_ns()-all_start),flush=True)
    print('FEED_CALL_NS\t'+str(percentile(durations,.5))+'\t'+str(percentile(durations,.95))+'\t'+str(max(durations) if durations else 0),flush=True)
    print('LIMIT\toffline_batch_throughput_not_camera_latency_or_control_acceptance',flush=True)

if __name__=='__main__':
    main()
