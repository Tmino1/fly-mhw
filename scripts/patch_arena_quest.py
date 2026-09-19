#!/usr/bin/env python3
"""
Side-project, not part of the ML pipeline: patches a real MHW custom-quest
.mib file (sourced from an existing Nexus mod, NOT extracted from the
game's own chunk archive — that needs Oodle's proprietary
oo2core_8_win64.dll, which this machine doesn't have, see
docs/architecture.md's "Starting a quest programmatically" entry for the
related AcceptQuest finding and docs/risks.md for the full story) into a
normal Great Jagras hunt in a fixed arena map, instead of chasing it
around the open world during recording sessions.

Format reverse-engineered directly from Aradi147/MHW-Quest's open-source
Quest Editor (github.com/Aradi147/MHW-Quest), not guessed, and confirmed
byte-for-byte correct against a real file: quest files are Blowfish ECB
(with a per-4-byte-word bswap wrapper on both sides) under a fixed,
hardcoded key; the decrypted quest_id field read back as exactly 90001,
matching the source file's own name (questData_90001.mib), before any
patching was applied.

Usage:
    python scripts/patch_arena_quest.py \
        --source /path/to/questData_90001.mib \
        --output /path/to/questData_90099.mib \
        --quest-id 90099 \
        --map arena_challenge

Only the .mib itself is patched here. The reward table (rem/remData_<id>.rem)
and localized name/description (common/text/quest/q<id>_<lang>.gmd) are
NOT — those formats weren't reverse-engineered this pass (they don't look
Blowfish-encrypted at all, based on a quick look, and appear to associate
to a quest purely by filename, not an embedded ID) — just copy the
source quest's files and rename the id in the filename to match. The
quest will show the SOURCE quest's original name/description in-game
until/unless those are patched too.
"""

from __future__ import annotations

import argparse
import struct
from pathlib import Path

from Crypto.Cipher import Blowfish

_KEY = b"TZNgJfzyD2WKiuV4SglmI6oN5jP2hhRJcBwzUooyfIUTM4ptDYGjuRTP"

# MapIDs table read directly from MainWindow.xaml.cs (Quest Editor source)
# — NOT the full list, just the arena-family maps this script cares about.
# 201 (Special Arena) is NOT on the game's ForbiddenMapIDs list for
# low/high rank quests; neither is 202.
_MAPS = {
    "special_arena": 201,
    "arena_challenge": 202,  # quicker run-up to the monster than Special Arena
}


def _bswap(data: bytes) -> bytes:
    out = bytearray(len(data))
    for i in range(0, len(data), 4):
        out[i], out[i + 1], out[i + 2], out[i + 3] = data[i + 3], data[i + 2], data[i + 1], data[i]
    return bytes(out)


def _crypt(data: bytes, encrypt: bool) -> bytes:
    data = _bswap(data)
    cipher = Blowfish.new(_KEY, Blowfish.MODE_ECB)
    padded_len = len(data) if len(data) % 8 == 0 else len(data) + 8 - (len(data) % 8)
    data = data.ljust(padded_len, b"\x00")
    data = cipher.encrypt(data) if encrypt else cipher.decrypt(data)
    return _bswap(data)


def decipher(data: bytes) -> bytes:
    return _crypt(data, encrypt=False)


def encipher(data: bytes) -> bytes:
    return _crypt(data, encrypt=True)


def patch_quest(
    plain: bytearray,
    quest_id: int,
    map_id: int,
    stars: int,
    rank: int,
    detemper_slots: bool,
) -> None:
    """Mutates plain (the decrypted quest struct, header included — offsets
    here are relative to byte 4, matching Quest Editor's own data2[] /
    ReadData[] distinction) in place."""

    def set_i32(off: int, val: int) -> None:
        struct.pack_into("<i", plain, 4 + off, val)

    set_i32(6, quest_id)
    plain[4 + 10] = stars
    plain[4 + 19] = rank
    set_i32(23, map_id)

    if detemper_slots:
        for slot in range(7):
            tempered_off = 4 + 184 + 65 * slot
            if plain[tempered_off] == 1:
                plain[tempered_off] = 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", required=True, help="an existing, real .mib file to patch (from a Nexus mod)")
    parser.add_argument("--output", required=True)
    parser.add_argument("--quest-id", type=int, required=True, help="new quest ID, convention is 90000+")
    parser.add_argument("--map", choices=sorted(_MAPS), default="arena_challenge")
    parser.add_argument("--stars", type=int, default=1)
    parser.add_argument("--rank", type=int, default=0, help="0=Low Rank, 1=High Rank, 2=Master Rank")
    parser.add_argument("--keep-tempered", action="store_true",
                         help="don't clear any monster slot's Tempered flag (default: clear all of them)")
    args = parser.parse_args()

    raw = Path(args.source).read_bytes()
    plain = bytearray(decipher(raw))

    before_id = struct.unpack_from("<i", plain, 4 + 6)[0]
    print(f"source quest_id (sanity check): {before_id}")

    patch_quest(
        plain,
        quest_id=args.quest_id,
        map_id=_MAPS[args.map],
        stars=args.stars,
        rank=args.rank,
        detemper_slots=not args.keep_tempered,
    )

    new_raw = encipher(bytes(plain))
    roundtrip = decipher(new_raw)
    assert roundtrip == bytes(plain), "round-trip mismatch after re-encryption — refusing to write a bad file"

    Path(args.output).write_bytes(new_raw)
    print(f"wrote {args.output} (quest_id={args.quest_id}, map={args.map}={_MAPS[args.map]})")
    print("NOTE: reward table (.rem) and name/description (.gmd) were not "
          "patched — copy the source quest's, renamed to this quest_id, if "
          "you want those to line up too. See this script's module "
          "docstring.")


if __name__ == "__main__":
    main()
