#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Patch gmrender-resurrect output_gstreamer.c for reliable DLNA track switching.

Problems being fixed (all observed with NetEase Cloud Music as control point):

 1. aborting the previous stream with set_state(READY) makes gstsouphttpsrc
    post a bogus bus error ("Could not establish connection to server" /
    "Internal data stream error"). The message is dispatched ~1ms *after*
    the next URI has been installed, so it lands on a freshly prerolling
    playbin and leaves it dead: silence, ALSA sink closed, UPnP still
    reporting PLAYING.
 2. several SetAVTransportURI in quick succession (user skipping tracks)
    each re-arm the playbin and interrupt each other's preroll.
 3. once the playbin is half torn down, a plain READY/PLAYING cycle cannot
    revive it.

Strategy:
 * coalesce a burst of SetAVTransportURI into a single rebuild (debounce)
 * do not interrupt a state transition that is still in flight
 * ignore the teardown-noise errors while a switch is in flight
 * verify afterwards that audio actually advances; otherwise recover the
   playbin through NULL (the only reliable way back)

The patch is idempotent: re-running it is a no-op.
"""

import re
import sys
import shutil

SRC = sys.argv[1] if len(sys.argv) > 1 else 'output_gstreamer.c'
MARKER = 'URI_SETTLE_MS'

def read():
    with open(SRC, 'r', encoding='utf-8') as f:
        return f.read()

def write(s):
    with open(SRC, 'w', encoding='utf-8') as f:
        f.write(s)

def replace_func(src, signature, new_text):
    """Replace a whole top-level function, located by its signature line."""
    i = src.find(signature)
    if i < 0:
        raise SystemExit('signature not found: ' + signature)
    j = src.find('\n}\n', i)
    if j < 0:
        raise SystemExit('end of function not found after: ' + signature)
    return src[:i] + new_text + src[j + 3:]

STATICS = '''
/* ------------------------------------------------------------------ *
 * Deferred stream switching
 * ------------------------------------------------------------------ */
#define URI_SETTLE_MS\t\t400
#define URI_WATCHDOG_MS\t\t4000
#define URI_WATCHDOG_CONFIRM_MS\t1200
#define URI_WATCHDOG_MAX_EXT\t3
#define URI_WATCHDOG_MAX_RESTART 2

static guint uri_switch_timer_ = 0;
static guint uri_watchdog_timer_ = 0;
static int uri_watchdog_stage_ = 0;
static int uri_watchdog_extensions_ = 0;
static int uri_watchdog_probes_ = 0;
static int uri_watchdog_resumes_ = 0;
static int uri_switch_restarts_ = 0;
static int uri_switch_settle_retries_ = 0;
static gint64 uri_watchdog_pos_ = -1;
static char *uri_applied_ = NULL;
static gboolean uri_switch_wants_play_ = FALSE;
static gboolean uri_switch_eos_ = FALSE;
static gint64 uri_switch_applied_us_ = 0;
static GMutex uri_switch_lock_;
/* kept so that a NULL rebuild can install the very same sink again instead
 * of silently falling back to autoaudiosink */
static GstElement *audio_sink_element_ = NULL;
'''

HELPERS = '''/*
 * NetEase Cloud Music selects a track with SetAVTransportURI and expects the
 * renderer to start it implicitly - it never follows up with Play - and it
 * fires several SetAVTransportURI in a row when the user skips through a
 * playlist. Re-arming the playbin for every single one of those is what
 * breaks playback:
 *
 *  - each PLAYING->READY transition aborts the previous stream\'s HTTP
 *    connection, and gstsouphttpsrc reports that abort as a bus error
 *    ("Could not establish connection to server" / "Internal data stream
 *    error"). The message is delivered *after* the next URI has been
 *    installed, so it hits a freshly prerolling pipeline and leaves it
 *    dead: silence, ALSA sink closed, UPnP still claiming PLAYING.
 *  - a state transition that is still on its way must not be interrupted,
 *    otherwise the preroll is thrown away and the pipeline never settles.
 *
 * So: coalesce the burst into one rebuild, ignore the teardown noise, and
 * verify afterwards that audio really advances. Recovering through NULL is
 * the only reliable way back from a half-torn-down playbin.
 *
 * Note on locking: libupnp serves SOAP actions from worker threads while the
 * timers below run in the GLib main loop. The lock therefore only guards the
 * bookkeeping variables - GStreamer is never called with it held.
 */
static gboolean uri_switch_in_flight(void) {
\treturn uri_switch_timer_ != 0 || uri_watchdog_timer_ != 0;
}

/* Mark the moment a stream was (re)started. An end-of-stream that arrives
 * shortly afterwards cannot belong to it - it is the stream we just replaced
 * still draining - and must not be reported as a stop. */
static void uri_switch_touch_applied(void) {
\turi_switch_applied_us_ = g_get_monotonic_time();
}

static void uri_switch_set_applied(const char *uri) {
\tif (uri_applied_ != NULL && uri != NULL && strcmp(uri_applied_, uri) == 0)
\t\treturn;
\tg_free(uri_applied_);
\turi_applied_ = uri ? g_strdup(uri) : NULL;
}

static gboolean uri_switch_eos_is_stale(void) {
\t/* A new uri is already queued, so this end-of-stream belongs to the
\t * stream that is about to be replaced. */
\tif (uri_switch_timer_ != 0)
\t\treturn TRUE;
\tif (gsuri_ == NULL || !uri_switch_wants_play_ ||
\t    uri_switch_applied_us_ == 0)
\t\treturn FALSE;
\treturn g_get_monotonic_time() - uri_switch_applied_us_ <
\t       G_USEC_PER_SEC * 5 / 2;
}

static void uri_switch_cancel_watchdog(void) {
\tif (uri_watchdog_timer_ != 0) {
\t\tg_source_remove(uri_watchdog_timer_);
\t\turi_watchdog_timer_ = 0;
\t}
\turi_watchdog_stage_ = 0;
\turi_watchdog_extensions_ = 0;
\turi_watchdog_probes_ = 0;
\turi_watchdog_resumes_ = 0;
\turi_watchdog_pos_ = -1;
}

static void uri_switch_cancel(void) {
\tif (uri_switch_timer_ != 0) {
\t\tg_source_remove(uri_switch_timer_);
\t\turi_switch_timer_ = 0;
\t}
\turi_switch_settle_retries_ = 0;
\turi_switch_cancel_watchdog();
}

/* Take a private copy of the current request, so that the callbacks can work
 * with stable data while libupnp threads keep rewriting the globals. */
static char *uri_switch_snapshot(gboolean *want_play) {
\tchar *uri;
\tg_mutex_lock(&uri_switch_lock_);
\turi = gsuri_ ? g_strdup(gsuri_) : NULL;
\tif (want_play)
\t\t*want_play = uri_switch_wants_play_;
\tg_mutex_unlock(&uri_switch_lock_);
\treturn uri;
}

static gboolean uri_switch_apply(gpointer data);
static gboolean uri_switch_watchdog(gpointer data);

/* Push uri into the playbin from scratch. Going through NULL throws away
 * every element of the previous pipeline, which is the only state from which
 * a half-torn-down playbin comes back to life reliably. */
static void uri_switch_restart(const char *why, const char *uri,
\t\t\t       gboolean want_play) {
\tconst GstState target = want_play ? GST_STATE_PLAYING : GST_STATE_PAUSED;
\tLog_info("gstreamer", "Restarting playbin (%s) \'%s\' -> state %d",
\t\t why, uri ? uri : "(null)", (int)target);
\tgst_element_set_state(player_, GST_STATE_NULL);
\tif (gst_element_set_state(player_, GST_STATE_READY) ==
\t    GST_STATE_CHANGE_FAILURE) {
\t\tLog_error("gstreamer", "READY failed while restarting playbin");
\t\treturn;
\t}
\tif (uri != NULL) {
\t\tg_object_set(G_OBJECT(player_), "uri", uri, NULL);
\t\turi_switch_set_applied(uri);
\t}
\tif (audio_sink_element_ != NULL)
\t\tg_object_set(G_OBJECT(player_), "audio-sink",
\t\t\t     audio_sink_element_, NULL);
\tif (gst_element_set_state(player_, target) ==
\t    GST_STATE_CHANGE_FAILURE) {
\t\tLog_error("gstreamer",
\t\t\t  "setting state failed while restarting playbin");
\t} else {
\t\turi_switch_eos_ = FALSE;
\t\turi_switch_touch_applied();
\t}
}

static void uri_switch_arm_watchdog(void) {
\turi_switch_cancel_watchdog();
\turi_watchdog_timer_ = g_timeout_add(URI_WATCHDOG_MS,
\t\t\t\t\t uri_switch_watchdog, NULL);
}

static void uri_switch_recover(const char *why) {
\tchar *uri;
\tgboolean want_play = FALSE;

\tif (uri_switch_restarts_ >= URI_WATCHDOG_MAX_RESTART) {
\t\tLog_error("gstreamer", "Playbin not recoverable (%s); giving up",
\t\t\t  why);
\t\treturn;
\t}
\turi_switch_restarts_++;
\turi = uri_switch_snapshot(&want_play);
\turi_switch_cancel_watchdog();
\turi_switch_restart(why, uri, want_play);
\tg_free(uri);
\turi_switch_arm_watchdog();
}

static gboolean uri_switch_watchdog(gpointer data) {
\tgint64 pos = -1;
\tGstState state = GST_STATE_NULL;
\tGstState pending = GST_STATE_VOID_PENDING;
\tgboolean want_play = FALSE;
\tchar *uri;

\t(void)data;
\turi_watchdog_timer_ = 0;

\tif (player_ == NULL || uri_switch_timer_ != 0)
\t\treturn G_SOURCE_REMOVE;

\turi = uri_switch_snapshot(&want_play);
\tif (uri == NULL) {
\t\tg_free(uri);
\t\treturn G_SOURCE_REMOVE;
\t}
\tg_free(uri);

\tgst_element_get_state(player_, &state, &pending, 0);

\tif (uri_watchdog_stage_ == 0) {
\t\tconst GstState wanted = want_play ? GST_STATE_PLAYING
\t\t\t\t\t\t  : GST_STATE_PAUSED;
\t\t/* Settled in pause although playback was asked for: a full
\t\t * rebuild would cause a needless gap, simply resume. */
\t\tif (want_play && state == GST_STATE_PAUSED &&
\t\t    pending == GST_STATE_VOID_PENDING &&
\t\t    uri_watchdog_resumes_++ < 2) {
\t\t\tLog_info("gstreamer",
\t\t\t\t "Settled in pause although play was requested; resuming");
\t\t\tgst_element_set_state(player_, GST_STATE_PLAYING);
\t\t\turi_watchdog_timer_ =
\t\t\t    g_timeout_add(URI_WATCHDOG_CONFIRM_MS,
\t\t\t\t\t  uri_switch_watchdog, NULL);
\t\t\treturn G_SOURCE_REMOVE;
\t\t}
\t\tif (state != wanted) {
\t\t\tif (pending == wanted &&
\t\t\t    uri_watchdog_extensions_ < URI_WATCHDOG_MAX_EXT) {
\t\t\t\turi_watchdog_extensions_++;
\t\t\t\tLog_info("gstreamer",
\t\t\t\t\t "Preroll still in flight (%d/%d); keep waiting",
\t\t\t\t\t uri_watchdog_extensions_,
\t\t\t\t\t URI_WATCHDOG_MAX_EXT);
\t\t\t\turi_watchdog_timer_ =
\t\t\t\t    g_timeout_add(URI_WATCHDOG_MS,
\t\t\t\t\t\t  uri_switch_watchdog, NULL);
\t\t\t\treturn G_SOURCE_REMOVE;
\t\t\t}
\t\t\tLog_info("gstreamer",
\t\t\t\t "Unexpected state %d (pending %d, wanted %d)",
\t\t\t\t (int)state, (int)pending, (int)wanted);
\t\t\turi_switch_recover("wrong-state");
\t\t\treturn G_SOURCE_REMOVE;
\t\t}
\t\tif (!want_play) {
\t\t\t/* Paused is a settled, legitimate state. */
\t\t\turi_switch_restarts_ = 0;
\t\t\treturn G_SOURCE_REMOVE;
\t\t}
\t\t/* The state alone is not enough: a playbin whose sink is gone stays
\t\t * in PLAYING while the position never moves. Check the clock too. */
\t\tif (gst_element_query_position(player_, GST_FORMAT_TIME, &pos))
\t\t\turi_watchdog_pos_ = pos;
\t\telse
\t\t\turi_watchdog_pos_ = -1;
\t\turi_watchdog_stage_ = 1;
\t\turi_watchdog_timer_ = g_timeout_add(URI_WATCHDOG_CONFIRM_MS,
\t\t\t\t\t\t    uri_switch_watchdog, NULL);
\t\treturn G_SOURCE_REMOVE;
\t}

\t/* stage 1: confirm that the stream is really moving. A single stalled
\t * sample is no proof of a dead pipeline - a network stall or a failed
\t * query looks exactly the same - so give it a couple more chances
\t * before throwing the stream away. */
\tif (uri_watchdog_pos_ >= 0 &&
\t    gst_element_query_position(player_, GST_FORMAT_TIME, &pos) &&
\t    pos > uri_watchdog_pos_ + 200 * GST_MSECOND) {
\t\turi_switch_restarts_ = 0;
\t\treturn G_SOURCE_REMOVE;
\t}
\tif (uri_watchdog_probes_++ < 2) {
\t\tLog_info("gstreamer",
\t\t\t "No progress yet (%" PRId64 " -> %" PRId64 "); re-checking",
\t\t\t (gint64)uri_watchdog_pos_, (gint64)pos);
\t\turi_watchdog_timer_ = g_timeout_add(URI_WATCHDOG_CONFIRM_MS,
\t\t\t\t\t\t    uri_switch_watchdog, NULL);
\t\treturn G_SOURCE_REMOVE;
\t}
\tLog_info("gstreamer", "Position not advancing (%" PRId64 " -> %" PRId64 ")",
\t\t (gint64)uri_watchdog_pos_, (gint64)pos);
\turi_switch_recover("stalled");
\treturn G_SOURCE_REMOVE;
}

static gboolean uri_switch_apply(gpointer data) {
\tGstState state = GST_STATE_NULL;
\tGstState pending = GST_STATE_VOID_PENDING;
\tGstState target;
\tgboolean want_play = FALSE;
\tchar *uri;

\t(void)data;
\turi_switch_timer_ = 0;
\tif (player_ == NULL)
\t\treturn G_SOURCE_REMOVE;

\turi = uri_switch_snapshot(&want_play);
\tif (uri == NULL) {
\t\t/* URI cleared: nothing left to play. */
\t\turi_switch_cancel_watchdog();
\t\tgst_element_set_state(player_, GST_STATE_READY);
\t\tg_free(uri);
\t\treturn G_SOURCE_REMOVE;
\t}

\tgst_element_get_state(player_, &state, &pending, 0);
\tif (pending != GST_STATE_VOID_PENDING && state != GST_STATE_PLAYING) {
\t\t/* A transition is still in flight. If it belongs to the uri we
\t\t * are about to install, give it a chance to settle; if it belongs
\t\t * to an older request, nobody is waiting for it and waiting would
\t\t * only delay the new track. */
\t\tconst gboolean own = (uri_applied_ != NULL &&
\t\t\t\t      strcmp(uri_applied_, uri) == 0);
\t\tif (own && uri_switch_settle_retries_++ < 15) {
\t\t\turi_switch_timer_ = g_timeout_add(120, uri_switch_apply,
\t\t\t\t\t\t\t  NULL);
\t\t\tg_free(uri);
\t\t\treturn G_SOURCE_REMOVE;
\t\t}
\t\tLog_info("gstreamer", "%s; forcing restart",
\t\t\t own ? "Pipeline never settled"
\t\t\t     : "Pending transition belongs to an older uri");
\t\turi_switch_settle_retries_ = 0;
\t\turi_switch_restart("settle-timeout", uri, want_play);
\t\turi_switch_arm_watchdog();
\t\tg_free(uri);
\t\treturn G_SOURCE_REMOVE;
\t}
\turi_switch_settle_retries_ = 0;
\turi_switch_restarts_ = 0;

\ttarget = want_play ? GST_STATE_PLAYING : GST_STATE_PAUSED;
\tLog_info("gstreamer", "Switching to \'%s\' (from %d, target %d)",
\t\t uri, (int)state, (int)target);

\tif (gst_element_set_state(player_, GST_STATE_READY) ==
\t    GST_STATE_CHANGE_FAILURE) {
\t\tLog_error("gstreamer", "READY failed while switching uri");
\t\turi_switch_restart("ready-failed", uri, want_play);
\t} else {
\t\tg_object_set(G_OBJECT(player_), "uri", uri, NULL);
\t\turi_switch_set_applied(uri);
\t\tif (gst_element_set_state(player_, target) ==
\t\t    GST_STATE_CHANGE_FAILURE) {
\t\t\tLog_error("gstreamer",
\t\t\t\t  "setting state %d failed after uri switch",
\t\t\t\t  (int)target);
\t\t\turi_switch_restart("state-failed", uri, want_play);
\t\t}
\t}
\turi_switch_eos_ = FALSE;
\turi_switch_touch_applied();
\turi_switch_arm_watchdog();
\tg_free(uri);
\treturn G_SOURCE_REMOVE;
}

'''

PLAY = '''static int output_gstreamer_play(output_transition_cb_t callback) {
\tGstState cur;

\tplay_trans_callback_ = callback;

\tg_mutex_lock(&uri_switch_lock_);
\t/* Play always means "want playing". Leaving this stale is what made the
\t * watchdog compare a playing pipeline against a "paused" intention. */
\turi_switch_wants_play_ = TRUE;
\tconst gboolean switch_queued = (uri_switch_timer_ != 0);
\tg_mutex_unlock(&uri_switch_lock_);
\tif (switch_queued) {
\t\t/* A queued switch is about to rebuild the playbin; let that
\t\t * rebuild start the stream instead of racing a second state
\t\t * change into a pipeline that is still being reset. */
\t\treturn 0;
\t}

\tcur = get_current_player_state();

\t/* Play is idempotent while a stream is running. Without this, a Play
\t * that arrives a little after SetAVTransportURI would rewind the track
\t * that has just prerolled. After end-of-stream the same Play must
\t * restart the track, hence the eos flag. */
\tif (cur == GST_STATE_PLAYING && !uri_switch_eos_) {
\t\turi_switch_arm_watchdog();
\t\treturn 0;
\t}

\t/* Resuming from PAUSE must not touch the uri: the uri that was set
\t * while paused is already installed in the playbin. */
\tif (cur == GST_STATE_PAUSED) {
\t\tif (gst_element_set_state(player_, GST_STATE_PLAYING) ==
\t\t    GST_STATE_CHANGE_FAILURE) {
\t\t\tLog_error("gstreamer", "setting play state failed (resume)");
\t\t\treturn -1;
\t\t}
\t\turi_switch_eos_ = FALSE;
\t\turi_switch_arm_watchdog();
\t\treturn 0;
\t}
\tif (gst_element_set_state(player_, GST_STATE_READY) ==
\t    GST_STATE_CHANGE_FAILURE) {
\t\tLog_error("gstreamer", "setting play state failed (1)");
\t\t// Error, but continue; can't get worse :)
\t}
\tg_object_set(G_OBJECT(player_), "uri", gsuri_, NULL);
\turi_switch_set_applied(gsuri_);
\tif (gst_element_set_state(player_, GST_STATE_PLAYING) ==
\t    GST_STATE_CHANGE_FAILURE) {
\t\tLog_error("gstreamer", "setting play state failed (2)");
\t\treturn -1;
\t}
\turi_switch_eos_ = FALSE;
\turi_switch_touch_applied();
\turi_switch_arm_watchdog();
\treturn 0;
}'''

STOP = '''static int output_gstreamer_stop(void) {
\tg_mutex_lock(&uri_switch_lock_);
\turi_switch_wants_play_ = FALSE;
\turi_switch_eos_ = FALSE;
\tg_mutex_unlock(&uri_switch_lock_);
\turi_switch_cancel();
\tif (gst_element_set_state(player_, GST_STATE_READY) ==
\t    GST_STATE_CHANGE_FAILURE) {
\t\treturn -1;
\t} else {
\t\treturn 0;
\t}
}'''

PAUSE = '''static int output_gstreamer_pause(void) {
\tg_mutex_lock(&uri_switch_lock_);
\turi_switch_wants_play_ = FALSE;
\tg_mutex_unlock(&uri_switch_lock_);
\t/* A paused renderer is a settled state; a pending watchdog would try to
\t * "repair" the very pause the user asked for. */
\turi_switch_cancel_watchdog();
\tif (gst_element_set_state(player_, GST_STATE_PAUSED) ==
\t    GST_STATE_CHANGE_FAILURE) {
\t\treturn -1;
\t} else {
\t\treturn 0;
\t}
}'''

SET_URI = '''static void output_gstreamer_set_uri(const char *uri,
\t\t\t\t     output_update_meta_cb_t meta_cb) {
\tg_mutex_lock(&uri_switch_lock_);
\tLog_info("gstreamer", "Set uri to \'%s\'", uri);
\tfree(gsuri_);
\tgsuri_ = (uri && *uri) ? strdup(uri) : NULL;
\tmeta_update_callback_ = meta_cb;
\tSongMetaData_clear(&song_meta_);
\tlast_known_time_.duration = 0;
\tlast_known_time_.position = 0;

\t/* A new uri must never downgrade the intent to play; only Pause and
\t * Stop may clear it. Sampling the state blindly is wrong here: control
\t * points send Seek(0) right before SetAVTransportURI, and a flushing
\t * seek puts the pipeline into PAUSED for a moment, so a playing session
\t * used to be turned into a silently paused one. */
\t{
\t\tconst GstState cur = get_current_player_state();
\t\tif (cur == GST_STATE_PLAYING)
\t\t\turi_switch_wants_play_ = TRUE;
\t\tLog_info("gstreamer", "Switch intent: state=%d want_play=%d%s",
\t\t\t (int)cur, uri_switch_wants_play_,
\t\t\t uri_switch_in_flight() ? " (in a burst)" : "");
\t}
\turi_switch_restarts_ = 0;
\turi_switch_settle_retries_ = 0;
\turi_switch_eos_ = FALSE;

\tif (uri_switch_timer_ != 0) {
\t\tg_source_remove(uri_switch_timer_);
\t\turi_switch_timer_ = 0;
\t}
\t/* Coalesce: only the last uri of a burst is rebuilt, which keeps the
\t * playbin from being torn down while it is still settling. */
\turi_switch_timer_ = g_timeout_add(URI_SETTLE_MS, uri_switch_apply, NULL);
\tg_mutex_unlock(&uri_switch_lock_);
}'''

BUS_ERR = '''\t\t/* While a switch is in flight the previous stream is torn down on
\t\t * purpose, and gstsouphttpsrc reports the aborted HTTP connection
\t\t * as an error. Those messages arrive right after the next uri has
\t\t * been installed; treating them as real failures would only fight
\t\t * the switch we just started. The watchdog still verifies that
\t\t * audio ends up running, so nothing is silently lost. */
\t\tif (uri_switch_in_flight()) {
\t\t\tLog_info("gstreamer",
\t\t\t\t "%s: teardown error ignored: %s (Debug: %s)",
\t\t\t\t msgSrcName, err->message, debug);
\t\t} else {
\t\t\tLog_error("gstreamer", "%s: Error: %s (Debug: %s)",
\t\t\t\t  msgSrcName, err->message, debug);
\t\t}'''

NEXT_URI = '''\tif (gs_next_uri_ == NULL) {
\t\t/* about-to-finish fires for every track, including the last one
\t\t * of a playlist where nothing was queued up. Stealing the NULL
\t\t * here would wipe the uri of the track that just finished, so a
\t\t * later Play would have nothing to start and fail with 704. */
\t\tLog_info("gstreamer",
\t\t\t "about-to-finish cb: no next uri, keeping the current one");
\t\treturn;
\t}
\tfree(gsuri_);
\tgsuri_ = gs_next_uri_;
\tgs_next_uri_ = NULL;
\tif (gsuri_ != NULL) {'''

def main():
    src = read()
    if MARKER in src:
        print('already patched, nothing to do')
        return 0

    shutil.copyfile(SRC, SRC + '.orig-switch')

    # 1. new static state, right after the last existing static
    anchor = 'static struct track_time_info last_known_time_ = {0, 0};\n'
    if anchor not in src:
        raise SystemExit('anchor for statics not found')
    src = src.replace(anchor, anchor + STATICS, 1)

    # 2. helpers + rewritten play(), plus stop/pause
    src = replace_func(src,
                       'static int output_gstreamer_play(output_transition_cb_t callback) {',
                       HELPERS + PLAY)
    src = replace_func(src, 'static int output_gstreamer_stop(void) {', STOP)
    src = replace_func(src, 'static int output_gstreamer_pause(void) {', PAUSE)

    # 3. deferred set_uri
    src = replace_func(src,
                       'static void output_gstreamer_set_uri(const char *uri,',
                       SET_URI)

    # 4. do not treat teardown noise as a real error
    pat = re.compile(
        r'\t\tLog_error\("gstreamer", "%s: Error: %s \(Debug: %s\)",\n'
        r'\t\t\t  msgSrcName, err->message, debug\);')
    if not pat.search(src):
        raise SystemExit('bus error log statement not found')
    src = pat.sub(lambda m: BUS_ERR, src, count=1)

    # 5. remember the explicitly configured sink, so that a NULL rebuild can
    #    install the very same element again
    pat5 = re.compile(
        r'\t\t  g_object_set \(G_OBJECT \(player_\), "audio-sink", sink, NULL\);')
    if not pat5.search(src):
        raise SystemExit('audio-sink assignment not found')
    src = pat5.sub(
        lambda m: m.group(0) +
        '\n\t\t  audio_sink_element_ = GST_ELEMENT(gst_object_ref(sink));',
        src, count=1)

    # 6. remember end-of-stream, so that a later Play really restarts the track
    pat6 = re.compile(
        r'(\tcase GST_MESSAGE_EOS:\n'
        r'\t\tLog_info\("gstreamer", "%s: End-of-stream", msgSrcName\);\n)')
    if not pat6.search(src):
        raise SystemExit('EOS case not found')
    src = pat6.sub(
        lambda m: m.group(1) +
        '\t\tif (uri_switch_eos_is_stale()) {\n'
        '\t\t\t/* The stream we replaced was still draining when the\n'
        '\t\t\t * new one started. Reporting this as a stop would put\n'
        '\t\t\t * the transport into STOPPED while audio is running. */\n'
        '\t\t\tLog_info("gstreamer",\n'
        '\t\t\t\t "EOS of the outgoing stream ignored");\n'
        '\t\t\tbreak;\n'
        '\t\t}\n'
        '\t\turi_switch_eos_ = TRUE;\n',
        src, count=1)

    # 7. never lose the current uri when no next uri was queued
    pat7 = re.compile(
        r'\tfree\(gsuri_\);\n\tgsuri_ = gs_next_uri_;\n'
        r'\tgs_next_uri_ = NULL;\n\tif \(gsuri_ != NULL\) \{')
    if not pat7.search(src):
        raise SystemExit('prepare_next_stream block not found')
    src = pat7.sub(lambda m: NEXT_URI, src, count=1)

    write(src)
    print('patched ok')
    return 0
if __name__ == '__main__':
    sys.exit(main())
