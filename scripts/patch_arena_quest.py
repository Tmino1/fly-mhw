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
    clear_extra_monsters: bool = True,
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

        # Arch Tempered is a SEPARATE, quest-level flag from the per-slot
        # Tempered checkbox above - packed at offset 130 as
        # 2*ATFlag + PSGear (0=neither, 1=PSGear, 2=AT, 3=both). Clearing
        # only the per-slot flag (as this function did originally) left
        # the AT health-bar border showing in-game even on a de-tempered
        # monster - caught live, 2026-09-19. Clear the AT bit, preserve
        # whatever PSGear was.
        plain[4 + 130] &= 0b01

    if clear_extra_monsters:
        # The source quest's slots 1-6 were never touched by earlier
        # patches, on the (wrong) assumption that a non-open-world map
        # just wouldn't spawn them. Caught live, 2026-09-19: the Arena
        # (Challenge) map DOES spawn them — slot 1 was "Seething
        # Bazelgeuse (IB)" (raw monster id 76) and slot 4 was "Tigrex
        # (IB)" (raw id 61), both large monsters showing up alongside the
        # intended Great Jagras. Setting a slot's monster id to -1
        # (SelectedIndex 0 = "None" in Quest Editor's own dropdown) empties
        # it, matching how slots 5-6 were already unset in the source file.
        for slot in range(1, 7):
            set_i32(172 + 65 * slot, -1)


def patch_gmd_text(data: bytearray, old: bytes, new: bytes) -> None:
    """In-place find-and-replace inside a quest .gmd (name/description)
    file. NOT Blowfish-encrypted (unlike .mib) — confirmed live,
    2026-09-19: the strings are plain, findable via a simple byte search.
    `new` must be no longer than `old` — this doesn't touch any length/
    offset tables elsewhere in the file (never reverse-engineered), so a
    same-length-or-shorter, null-padded replacement is the only safe
    option here."""
    idx = data.find(old)
    if idx == -1:
        raise ValueError(f"{old!r} not found in this .gmd")
    if len(new) > len(old):
        raise ValueError(f"replacement {new!r} ({len(new)} bytes) is longer than {old!r} ({len(old)} bytes)")
    data[idx:idx + len(old)] = new.ljust(len(old), b"\x00")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", help="an existing, real .mib file to patch (from a Nexus mod) — "
                                          "required unless --skip-mib")
    parser.add_argument("--output")
    parser.add_argument("--quest-id", type=int, help="new quest ID, convention is 90000+ — required unless --skip-mib")
    parser.add_argument("--map", choices=sorted(_MAPS), default="arena_challenge")
    parser.add_argument("--stars", type=int, default=1,
                         help="IMPORTANT: this is what actually gates Master Rank for quests loaded via "
                              "Strackeror/MHW-QuestLoader (github.com/Strackeror/MHW-QuestLoader), confirmed "
                              "live 2026-09-19 by reading its source directly — its own is_master_rank hook "
                              "returns `stars > 10`, ignoring the --rank byte below entirely for that purpose. "
                              "Use 16 (the source Arch Tempered quest's own original value, confirmed working) "
                              "for a Master Rank quest; anything <=10 is Low/High Rank regardless of --rank.")
    parser.add_argument("--rank", type=int, default=0,
                         help="0=Low Rank, 1=High Rank, 2=Master Rank — cosmetic/consistency only if loading "
                              "via QuestLoader (see --stars); real MHW presumably still uses this normally")
    parser.add_argument("--keep-tempered", action="store_true",
                         help="don't clear any monster slot's Tempered flag, or the separate quest-level "
                              "Arch Tempered flag at offset 130 (default: clear both)")
    parser.add_argument("--keep-extra-monsters", action="store_true",
                         help="don't clear monster slots 1-6, leaving whatever the source quest had there "
                              "(default: clear them, keeping only slot 0). The source Arch Tempered Great "
                              "Jagras quest had two other large monsters and small wildlife in these slots, "
                              "which DO spawn on other maps too, not just the source's original one.")
    parser.add_argument("--skip-mib", action="store_true",
                         help="skip .mib patching entirely — use with --gmd-* to only patch text")
    parser.add_argument("--gmd-source", help="a .gmd (name/description) file to also patch, in place semantics "
                                              "the same as --source/--output")
    parser.add_argument("--gmd-output")
    parser.add_argument("--gmd-replace", action="append", default=[], metavar="OLD=NEW",
                         help="text replacement inside --gmd-source, repeatable. NEW must be no longer than "
                              "OLD in raw UTF-8 bytes (see patch_gmd_text's docstring for why).")
    args = parser.parse_args()

    if not args.skip_mib and not (args.source and args.output and args.quest_id):
        parser.error("--source/--output/--quest-id are required unless --skip-mib is given")

    if not args.skip_mib:
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
            clear_extra_monsters=not args.keep_extra_monsters,
        )

        new_raw = encipher(bytes(plain))
        roundtrip = decipher(new_raw)
        assert roundtrip == bytes(plain), "round-trip mismatch after re-encryption — refusing to write a bad file"

        Path(args.output).write_bytes(new_raw)
        print(f"wrote {args.output} (quest_id={args.quest_id}, map={args.map}={_MAPS[args.map]})")
        print("NOTE: reward table (.rem) was not patched — copy the source "
              "quest's, renamed to this quest_id, if needed.")

    if args.gmd_source:
        gmd = bytearray(Path(args.gmd_source).read_bytes())
        for replacement in args.gmd_replace:
            old, _, new = replacement.partition("=")
            patch_gmd_text(gmd, old.encode("utf-8"), new.encode("utf-8"))
        Path(args.gmd_output).write_bytes(gmd)
        print(f"wrote {args.gmd_output} ({len(args.gmd_replace)} text replacement(s))")


if __name__ == "__main__":
    main()
