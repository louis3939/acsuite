"""Frame-based cutting/trimming/splicing of audio with VapourSynth and FFmpeg."""
__all__ = ['eztrim']
__author__ = 'Dave <orangechannel@pm.me>'
__date__ = '10 August 2020'
__credits__ = """AzraelNewtype, for the original audiocutter.py.
Ricardo Constantino (wiiaboo), for vfr.py from which this was inspired.
doop, for explaining the use of None for empty slicing
Vardë, for fixing FFmpeg's >4GB WAV file issues
"""
__version__ = '5.2.0'

import collections
import fractions
import functools
import os
import pathlib
import subprocess
from shutil import which
from subprocess import run
from typing import Dict, List, Optional, Tuple, Union
from warnings import simplefilter, warn

import vapoursynth as vs

simplefilter('always')  # display warnings

Path = Union[bytes, os.PathLike, pathlib.Path, str]
Trim = Tuple[Optional[int], Optional[int]]


def eztrim(clip: vs.VideoNode,
           /,
           trims: Union[List[Trim], Trim],
           audio_file: Path,
           outfile: Optional[Path] = None,
           *,
           ffmpeg_path: Optional[Path] = None,
           quiet: bool = False,
           timecodes_file: Optional[Path] = None,
           debug: bool = False
           ) -> Optional[Dict[str, Union[int, List[int], List[str]]]]:
    """
    Simple trimming function that follows VapourSynth/Python slicing syntax.

    End frame is NOT inclusive.

    For a 100 frame long VapourSynth clip:

    >>> src = core.ffms2.Source('file.mkv')
    >>> clip = src[3:22]+src[23:40]+src[48]+src[50:-20]+src[-10:-5]+src[97:]
    >>> 'These trims can be almost directly entered as:'
    >>> trims = [(3, 22), (23, 40), (48, 49), (50, -20), (-10, -5), (97, None)]
    >>> eztrim(src, trims, 'audio_file.wav')

    >>> src = core.ffms2.Source('file.mkv')
    >>> clip = src[3:-13]
    >>> 'A single slice can be entered as a single tuple:'
    >>> eztrim(src, (3, -13), 'audio_file.wav')


    :param clip:          Input clip needed to determine framerate for audio timecodes
                          and ``clip.num_frames`` for negative indexing
    :param trims:         Either a list of 2-tuples, or one tuple of 2 ints
        Empty slicing must represented with a ``None``.
            ``src[:10]+src[-5:]`` must be entered as ``trims=[(None, 10), (-5, None)]``.
        For legacy reasons, ``0`` can be used in place of ``None`` but is not recommended.
        Single frame slices must be represented as a normal slice.
            ``src[15]`` must be entered as ``trims=(15, 16)``.
    :param audio_file:    A string or path-like object refering to the source audio file's location
                          (i.e. '/path/to/audio_file.ext').
                          If the extension is not recognized as a valid audio file extension for FFmpeg's encoders,
                          the audio will be re-encoded to WAV losslessly.
    :param outfile:       Either a filename 'out.ext' or a full path '/path/to/out.ext'
                          that will be used for the trimmed audio file.
                          The extension will be automatically inserted for you,
                          and if it is given, it will be overwritten by the input `audio_file`'s extension.
                          If left blank, defaults to ``audio_file_cut.ext``.

    :param ffmpeg_path: Set this if ``ffmpeg`` is not in your `PATH`.
                        If ``ffmpeg`` exists in your `PATH`, it will automatically be detected and used.

    :param quiet:         Suppresses most console output from FFmpeg
    :param timecodes_file: Timecodes v2 file (generated by vspipe, ffms2, etc.) for variable-frame-rate clips.
                           Not needed for CFR clips.
    :param debug:         Used for testing purposes
    """
    if debug:
        pass
    else:
        if not os.path.isfile(audio_file):
            raise FileNotFoundError(f"eztrim: {audio_file} not found")

        audio_file_name, audio_file_ext = os.path.splitext(audio_file)
        ffmpeg_valid_encoder_extensions = {
            '.aac', '.m4a', '.adts',
            '.ac3',
            '.alac', '.caf',
            '.dca', '.dts',
            '.eac3',
            '.flac',
            '.gsm',
            '.mlp',
            '.mp2', '.mp3', '.mpga',
            '.opus', '.spx', '.ogg', '.oga'
            '.pcm', '.raw',
            '.sbc',
            '.thd',
            '.tta',
            '.wav', 'w64',
            '.wma',
        }
        if audio_file_ext not in ffmpeg_valid_encoder_extensions:
            warn(f"{audio_file_ext} is not a supported extension by FFmpeg's audio encoders, re-encoding to WAV", Warning)
            audio_file_ext = '.wav'
            codec_args = []
        else:
            codec_args = ['-c:a', 'copy', '-rf64', 'auto']

        if outfile is None:
            outfile = audio_file_name + '_cut' + audio_file_ext
        elif not os.path.splitext(outfile)[1]:
            outfile += audio_file_ext
        elif os.path.splitext(outfile)[-1] != audio_file_ext:
            outfile = os.path.splitext(outfile)[0] + audio_file_ext

        if os.path.isfile(outfile):
            raise FileExistsError(f"eztrim: {outfile} already exists")

        if ffmpeg_path is None:
            ffmpeg_path = which('ffmpeg')
        else:
            if not os.path.isfile(ffmpeg_path):
                raise FileNotFoundError(f"eztrim: ffmpeg executable at {ffmpeg_path} not found")
            try:
                args = ['ffmpeg', '-version']
                if subprocess.run(args, stdout=subprocess.PIPE, text=True).stdout.split()[0] != 'ffmpeg':
                    raise ValueError("eztrim: ffmpeg executable not working properly")
            except FileNotFoundError:
                raise FileNotFoundError("eztrim: ffmpeg executable not found in PATH") from None

        if timecodes_file is not None and not os.path.isfile(timecodes_file):
            raise FileNotFoundError(f"eztrim: {timecodes_file} not found")

    # error checking ------------------------------------------------------------------------
    if not isinstance(trims, (list, tuple)):
        raise TypeError("eztrim: trims must be a list of 2-tuples (or just one 2-tuple)")

    if len(trims) == 1 and isinstance(trims, list):
        warn("eztrim: using a list of one 2-tuple is not recommended; "
             "for a single trim, directly use a tuple: `trims=(5,-2)` instead of `trims=[(5,-2)]`", SyntaxWarning)
        if isinstance(trims[0], tuple):
            trims = trims[0]
        else:
            raise ValueError("eztrim: the inner trim must be a tuple")
    elif isinstance(trims, list):
        for trim in trims:
            if not isinstance(trim, tuple):
                raise TypeError(f"eztrim: the trim {trim} is not a tuple")
            if len(trim) != 2:
                raise ValueError(f"eztrim: the trim {trim} needs 2 elements")
            for i in trim:
                if not isinstance(i, (int, type(None))):
                    raise ValueError(f"eztrim: the trim {trim} must have 2 ints or None's")

    if isinstance(trims, tuple):
        if len(trims) != 2:
            raise ValueError("eztrim: a single tuple trim must have 2 elements")
    # --------------------------------------------

    num_frames = clip.num_frames
    ts = functools.partial(f2ts, timecodes_file=timecodes_file, src_clip=clip)

    if isinstance(trims, tuple):
        start, end = _negative_to_positive(num_frames, *trims)
        if end <= start:
            raise ValueError(f"eztrim: the trim {trims} is not logical")
        cut_ts_s: List[str] = [ts(start)]
        cut_ts_e: List[str] = [ts(end)]
        if debug:
            return {'s': start, 'e': end, 'cut_ts_s': cut_ts_s, 'cut_ts_e': cut_ts_e}
    else:
        starts, ends = _negative_to_positive(num_frames, [s for s, e in trims], [e for s, e in trims])
        if _check_ordered(starts, ends):
            cut_ts_s = [ts(f) for f in starts]
            cut_ts_e = [ts(f) for f in ends]
        else:
            raise ValueError("eztrim: the trims are not logical")
        if debug:
            return {'s': starts, 'e': ends, 'cut_ts_s': cut_ts_s, 'cut_ts_e': cut_ts_e}

    ffmpeg_silence = [str(ffmpeg_path), '-hide_banner', '-loglevel', '16'] if quiet else [str(ffmpeg_path), '-hide_banner']

    if len(cut_ts_s) == 1:
        args = ffmpeg_silence + ['-i', audio_file, '-vn', '-ss', cut_ts_s[0], '-to', cut_ts_e[0]] + codec_args + [outfile]
        run(args)
        return

    times = [[s, e] for s, e in zip(cut_ts_s, cut_ts_e)]
    if os.path.isfile('_acsuite_temp_concat.txt'):
        raise ValueError("_acsuite_temp_concat.txt already exists, quitting")
    else:
        concat_file = open('_acsuite_temp_concat.txt', 'w')
        temp_filelist = []
    for key, time in enumerate(times):
        outfile_tmp = f'_acsuite_temp_output_{key}' + os.path.splitext(outfile)[-1]
        concat_file.write(f"file {outfile_tmp}\n")
        temp_filelist.append(outfile_tmp)
        args = ffmpeg_silence + ['-i', audio_file, '-vn', '-ss', time[0], '-to', time[1]] + codec_args + [outfile_tmp]
        run(args)

    concat_file.close()
    args = ffmpeg_silence + ['-f', 'concat', '-i', '_acsuite_temp_concat.txt', '-c', 'copy', outfile]
    run(args)

    os.remove('_acsuite_temp_concat.txt')
    for file in temp_filelist:
        os.remove(file)


def f2ts(f: int, /, *, precision: int = 3, timecodes_file: Optional[Path] = None, src_clip: vs.VideoNode) -> str:
    """Converts frame number to a timestamp based on framerate.

    Can handle variable-frame-rate clips as well, using similar methods to that of vspipe --timecodes.
    For VFR clips, will use a timecodes v2 file if given, else will fallback to the slower frames() method.
    Meant to be called as a functools.partial with 'src_clip' specified before-hand.
    """
    if precision not in [0, 3, 6, 9]:
        raise ValueError(f"f2ts: the precision {precision} must be a multiple of 3 (including 0)")
    if src_clip.fps != fractions.Fraction(0, 1):
        t = round(10 ** 9 * f * src_clip.fps ** -1)
        s = t / 10 ** 9
    else:
        if timecodes_file is not None:
            timecodes = [float(x)/1000 for x in open(timecodes_file, 'r').read().splitlines()[1:]]
            s = timecodes[f]
        else:
            s = clip_to_timecodes(src_clip)[f]

    m = s // 60
    s %= 60
    h = m // 60
    m %= 60

    if precision == 0:
        return f'{h:02.0f}:{m:02.0f}:{round(s):02}'
    elif precision == 3:
        return f'{h:02.0f}:{m:02.0f}:{s:06.3f}'
    elif precision == 6:
        return f'{h:02.0f}:{m:02.0f}:{s:09.6f}'
    elif precision == 9:
        return f'{h:02.0f}:{m:02.0f}:{s:012.9f}'


@functools.lru_cache
def clip_to_timecodes(src_clip: vs.VideoNode) -> collections.deque:
    """Cached function to return a list of timecodes for vfr clips."""
    timecodes = collections.deque([0.0], maxlen=src_clip.num_frames + 1)
    curr_time = fractions.Fraction()
    init_percentage = 0
    for frame in src_clip.frames():
        curr_time += fractions.Fraction(frame.props['_DurationNum'], frame.props['_DurationDen'])
        timecodes.append(float(curr_time))
        percentage_done = round(100 * len(timecodes) / src_clip.num_frames)
        if percentage_done % 10 == 0 and percentage_done != init_percentage:
            print(rf"Finding timecodes for variable-framerate clip: {percentage_done}% done")
            init_percentage = percentage_done
    return timecodes


_Neg2pos_in = Union[List[Optional[int]], Optional[int]]
_Neg2pos_out = Union[Tuple[List[int], List[int]], Tuple[int, int]]


def _negative_to_positive(num_frames: int, a: _Neg2pos_in, b: _Neg2pos_in) -> _Neg2pos_out:
    """Changes negative/zero index to positive based on num_frames."""
    single_trim = (isinstance(a, (int, type(None))) and isinstance(b, (int, type(None))))

    # simplify analysis of a single trim
    if single_trim:
        a, b = (a or 0), (b or 0)
        if abs(a) > num_frames or abs(b) > num_frames:
            raise ValueError(f"_negative_to_positive: {max(abs(a), abs(b))} is out of bounds")
        return a if a >= 0 else num_frames + a, b if b > 0 else num_frames + b

    else:
        if len(a) != len(b):
            raise ValueError("_negative_to_positive: lists must be same length")

        real_a, real_b = [(i or 0) for i in a], [(i or 0) for i in b]  # convert None to 0

        if not (all(abs(i) <= num_frames for i in real_a) and all(abs(i) <= num_frames for i in real_b)):
            raise ValueError("_negative_to_positive: one or more trims are out of bounds")

        if all(i >= 0 for i in real_a) and all(i > 0 for i in real_b):
            return real_a, real_b

        positive_a = [x if x >= 0 else num_frames + x for x in real_a]
        positive_b = [y if y > 0 else num_frames + y for y in real_b]

        return positive_a, positive_b


def _check_ordered(starts: List[int], ends: List[int]) -> bool:
    """Checks if lists follow logical Python slicing."""
    if not all(starts[i] < ends[i] for i in range(len(starts))):
        return False  # makes sure pair is at least one frame long
    if not all(ends[i] < starts[i + 1] for i in range(len(starts) - 1)):
        return False  # checks if all ends are less than next start
    return True
