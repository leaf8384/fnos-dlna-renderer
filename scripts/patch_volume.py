#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Patch gmrender-resurrect upnp_control.c so that the volume survives restarts.

upnp_control_init() seeds the RenderingControl variables from the output sink,
which is always at full scale on a fresh start, so every restart the renderer
comes up at volume #100 (0 dB). A control point that does not push its own
volume on connect (NetEase does not) therefore blasts at full volume until the
user drags the slider once.

Store the last level that was applied and restore it on start instead.

Idempotent: re-running it is a no-op.
"""

import re
import sys
import shutil

SRC = sys.argv[1] if len(sys.argv) > 1 else 'upnp_control.c'
MARKER = 'VOLUME_STATE_FILE'

HELPERS = '''/* Volume that the user last chose. upnp_control_init() can only seed from the
 * output sink, which is at full scale on every fresh start, so without this
 * the renderer always comes up at 100% and the control point hears full
 * volume until somebody touches the slider. */
#define VOLUME_STATE_FILE "/var/lib/gmediarender/volume.state"

static void volume_state_save(int level) {
\tFILE *f = fopen(VOLUME_STATE_FILE, "w");
\tif (f == NULL) {
\t\treturn;
\t}
\tfprintf(f, "%d\\n", level);
\tfclose(f);
}

/* Returns the stored level, or -1 if there is nothing usable. */
static int volume_state_restore(void) {
\tchar line[16];
\tFILE *f = fopen(VOLUME_STATE_FILE, "r");
\tint level = -1;

\tif (f == NULL) {
\t\treturn -1;
\t}
\tif (fgets(line, sizeof(line), f) != NULL) {
\t\tlevel = atoi(line);
\t}
\tfclose(f);
\tif (level < volume_range.min || level > volume_range.max) {
\t\treturn -1;
\t}
\treturn level;
}

static void volume_apply_level(int level, const char *why) {
\tconst float decibel = volume_level_to_decibel(level);
\tchar volume[16];
\tchar db_volume[16];

\tsnprintf(volume, sizeof(volume), "%d", level);
\tsnprintf(db_volume, sizeof(db_volume), "%d", (int) (256 * decibel));
\tLog_info("control", "%s: level #%d (%.2fdb)", why, level, decibel);

\tchange_volume(volume, db_volume);
\toutput_set_volume(exp(decibel / 20 * log(10)));
\tset_mute_toggle(level == 0);
}

'''

INIT = '''void upnp_control_init(struct upnp_device *device) {
\tstruct service *service = upnp_control_get_service();
\tconst int saved_level = volume_state_restore();

\tif (saved_level >= 0) {
\t\t/* Come up at the volume the user last chose instead of full
\t\t * scale, so the first thing that is cast is not a shock. */
\t\tvolume_apply_level(saved_level, "Restoring saved volume");
\t} else {
\t\t// Set initial volume.
\t\tfloat volume_fraction = 0;
\t\tif (output_get_volume(&volume_fraction) == 0) {
\t\t\tLog_info("control", "Output initial volume is %f; setting "
\t\t\t\t "control variables accordingly.",
\t\t\t\t volume_fraction);
\t\t\tchange_volume_decibel(20 * log(volume_fraction) / log(10));
\t\t}
\t}

\tassert(service->last_change == NULL);'''


def main():
    src = open(SRC, 'r', encoding='utf-8').read()
    if MARKER in src:
        print('already patched, nothing to do')
        return 0

    shutil.copyfile(SRC, SRC + '.orig-volume')

    # 1. helpers, placed right before upnp_control_init() so that every
    #    function they call is already declared above
    anchor = 'void upnp_control_init(struct upnp_device *device) {'
    if anchor not in src:
        raise SystemExit('upnp_control_init anchor not found')
    src = src.replace(anchor, HELPERS + anchor, 1)

    # 2. remember every applied level
    body = ('static void change_volume(const char *volume, const char *db_volume) {\n'
            '\treplace_var(CONTROL_VAR_VOLUME, volume);\n'
            '\treplace_var(CONTROL_VAR_VOLUME_DB, db_volume);\n}')
    if body not in src:
        raise SystemExit('change_volume body not found')
    src = src.replace(
        body,
        'static void volume_state_save(int level);\n\n'
        'static void change_volume(const char *volume, const char *db_volume) {\n'
        '\treplace_var(CONTROL_VAR_VOLUME, volume);\n'
        '\treplace_var(CONTROL_VAR_VOLUME_DB, db_volume);\n'
        '\tvolume_state_save(atoi(volume));\n'
        '}', 1)

    # 3. restore on start, only fall back to the sink when nothing is stored
    pat = re.compile(
        r'void upnp_control_init\(struct upnp_device \*device\) \{\n'
        r'\tstruct service \*service = upnp_control_get_service\(\);\n\n'
        r'\t// Set initial volume\.\n'
        r'\tfloat volume_fraction = 0;\n'
        r'\tif \(output_get_volume\(&volume_fraction\) == 0\) \{\n'
        r'\t\tLog_info\("control", "Output initial volume is %f; setting "\n'
        r'\t\t\t "control variables accordingly\.", volume_fraction\);\n'
        r'\t\tchange_volume_decibel\(20 \* log\(volume_fraction\) / log\(10\)\);\n'
        r'\t\}\n')
    if not pat.search(src):
        raise SystemExit('upnp_control_init seeding block not found')
    src = pat.sub(lambda m: INIT, src, count=1)

    open(SRC, 'w', encoding='utf-8').write(src)
    print('patched ok')
    return 0


if __name__ == '__main__':
    sys.exit(main())
