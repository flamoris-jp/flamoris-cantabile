#!/usr/bin/env python3
"""Create a small original editable project; no packages, audio assets or APIs."""
import argparse
import json
import pathlib


def demo():
    def raw(tick, status, data):
        return dict(tick=tick, kind='midi', status=status, data=data)
    def note(tick, pitch, duration, velocity=85):
        return dict(tick=tick, kind='note', pitch=pitch, duration=duration, velocity=velocity)
    conductor = dict(id='conductor', name='Tempo', channel=0, end_tick=15360, events=[
        dict(tick=0, kind='meta', meta_type=81, data=list((60000000 // 128).to_bytes(3, 'big'))),
        dict(tick=0, kind='meta', meta_type=88, data=[4, 2, 24, 8]),
        dict(tick=7680, kind='meta', meta_type=81, data=list((60000000 // 140).to_bytes(3, 'big')))])
    lead = dict(id='lead', name='Melody', channel=0, events=[raw(0, 0xC0, [80])])
    chords = dict(id='chords', name='Chords', channel=1, events=[raw(0, 0xC1, [0])])
    bass = dict(id='bass', name='Bass', channel=2, events=[raw(0, 0xC2, [33])])
    drums = dict(id='drums', name='Drums', channel=9, events=[])
    harmony = [(57, 60, 64), (53, 57, 60), (50, 53, 57), (52, 56, 59)]
    melody = [(69, 72, 76, 74), (72, 69, 67, 69), (74, 72, 69, 65), (68, 71, 76, 68)]
    for bar in range(8):
        start = bar * 1920
        chord = harmony[bar % 4]
        for pitch in chord:
            chords['events'].append(note(start, pitch, 1800, 64))
        for beat in range(4):
            lead['events'].append(note(start + beat * 480, melody[bar % 4][beat], 360 if bar < 4 else 240))
            bass['events'].append(note(start + beat * 480 + (120 if beat % 2 and bar >= 4 else 0), chord[0] - 12, 240, 90))
            drums['events'].append(note(start + beat * 480, 36 if beat % 2 == 0 else 38, 120, 88))
            drums['events'].append(note(start + beat * 480, 42, 60, 55))
            drums['events'].append(note(start + beat * 480 + 240, 42, 60, 42))
    for track in (lead, chords, bass, drums):
        track['end_tick'] = 15360
    return dict(schema_version=1, name='Cantabile First Steps', ppq=480, smf_format=1,
                tracks=[conductor, lead, chords, bass, drums])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=pathlib.Path)
    args = parser.parse_args()
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(demo(), stream, ensure_ascii=False, indent=2)
        stream.write('\n')
