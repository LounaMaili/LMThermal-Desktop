"""Small explicit recording histories derived from existing sanitized frames."""

from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from radiometric_recorder import RadiometricRecorder
from radiometric_session import FrameObservation, SessionState, inspect_frame, make_measurement
from roi_measurement import NativeROI


def manual_sampler(recorder):
    recorder._stop.wait()
    with recorder._journal_lock:
        recorder._timeline.flush()
        recorder._timeline.close()
        recorder._sampler_done.set()


def observations():
    fixtures=Path(__file__).parent/'fixtures'
    result=[]
    for name in ('radiometric-room-settled.raw','warm-hand-settled.raw'):
        raw=(fixtures/name).read_bytes()
        result.append(FrameObservation(raw,0,SessionState.RADIOMETRIC_READY,
                                       inspect_frame(raw),False,None,make_measurement(raw,0)))
    return result


def build_bundle(path, frames=None, *, rate=5, count=9, chunk_frames=2):
    """Includes nonuniform time, a session gap, overload drop, ROI change and clear."""
    frames=frames or observations()
    with patch.object(RadiometricRecorder,'_sample_loop',manual_sampler):
        recorder=RadiometricRecorder(path,rate_hz=rate,chunk_frames=chunk_frames)
        recorder.manifest['validation_provenance']='Fixture-derived test history; no new camera acquisition'
        for i in range(1,count+1):
            frame=replace(frames[0 if i < count//2 else 1])
            if i % 11 == 3 or i == 3:
                frame=replace(frame,state=SessionState.SHUTTER_TRANSIENT,rejection='held_image')
            roi=NativeROI(160,180,220,240) if i < count//2 else NativeROI(10,10,30,30) if i < count*3//4 else None
            recorder.update_latest(frame,roi)
            # Intentional simulated writer overload; never presented as a live result.
            with patch.object(recorder.handoff,'full',return_value=True) if i%13==5 else patch.object(recorder.handoff,'full',wraps=recorder.handoff.full):
                recorder.record_instant(i,recorder.started+i/rate+(i//4)*.012)
            recorder.handoff.join()
        recorder.stop()
    return path
