#!/usr/bin/env python3
"""Run the actual patched volume scheduler against observable fake outputs.

Usage: python3 tests/test_volume_sequence.py build/gmrender-resurrect-master
Requires a C compiler and GLib development files; no speaker or NAS changes.
"""
import pathlib, subprocess, sys, tempfile

source = pathlib.Path(sys.argv[1]) / 'src/upnp_control.c'
text = source.read_text()
sequence = text[text.index('/* A request publishes'):text.index('static void change_volume(const char *volume')]
harness = r'''
#include <glib.h>
#include <pthread.h>
#include <math.h>
#include <assert.h>
#include <stdio.h>
#define Log_info(...) ((void)0)
static pthread_mutex_t control_mutex = PTHREAD_MUTEX_INITIALIZER;
static double gain = .2, values[200];
static int muted, playing = 1, transport_generation, count, beeps, fail_beep;
static gint64 changed[200], beep_end, begun;
static int output_set_volume(float v) { gain=v; values[count]=v; changed[count++]=g_get_monotonic_time(); return 0; }
static int output_get_volume(float *v) { *v=gain; return 0; }
static int output_get_mute(int *v) { *v=muted; return 0; }
static void set_mute_toggle(int v) {muted=v;}
static int upnp_transport_is_playing(void) {return playing;}
static int upnp_transport_generation(void) {return transport_generation;}
typedef struct {int (*valid)(void*);void (*done)(void*);void *data;} fake_beep;
static gboolean fake_done(gpointer p) {
 fake_beep *b=p; beep_end=g_get_monotonic_time();b->done(b->data);return G_SOURCE_REMOVE;
}
static void output_beep(int which,int duration,float old,int(*valid)(void*),void(*done)(void*),void *data){
 fake_beep *b=g_new0(fake_beep,1);b->valid=valid;b->done=done;b->data=data;
 assert(fabs(old-gain)<.00001);beeps++;
 g_timeout_add_full(G_PRIORITY_DEFAULT,fail_beep?1:duration+40,fake_done,b,g_free);
}
'''
tests = r'''
static void pump(int ms){gint64 end=g_get_monotonic_time()+ms*1000;do{while(g_main_context_iteration(NULL,FALSE)){}g_usleep(1000);}while(g_get_monotonic_time()<end);}
static void request(int level,double target){pthread_mutex_lock(&control_mutex);volume_request_locked(level,target);pthread_mutex_unlock(&control_mutex);}
static void reset(void){pthread_mutex_lock(&control_mutex);volume_cancel_locked();pthread_mutex_unlock(&control_mutex);pump(160);count=beeps=0;gain=.2;muted=0;playing=1;volume_target_level_=-1;fail_beep=0;begun=g_get_monotonic_time();}
int main(void){
 reset();request(80,.4);pump(220);assert(beeps==1&&count==0);pump(330);
 assert(count==12&&fabs(gain-.4)<.00001&&changed[0]>beep_end);
 for(int i=1;i<count;i++)assert(values[i]>=values[i-1]);
 puts("PASS tone completes before one monotonic volume ramp");
 reset();request(80,.4);pump(210);request(30,.03);pump(600);
 assert(fabs(gain-.03)<.00001);for(int i=1;i<count;i++)assert(values[i]<=values[i-1]);
 puts("PASS superseded completion cannot restore an old target");
 reset();request(80,.4);pump(210);playing=0;transport_generation++;pump(170);
 assert(count==1&&fabs(gain-.4)<.00001);puts("PASS stop settles target without a delayed ramp");
 reset();request(80,.4);pump(210);pthread_mutex_lock(&control_mutex);volume_cancel_locked();output_set_volume(volume_target_gain_);set_mute_toggle(1);pthread_mutex_unlock(&control_mutex);pump(400);
 assert(count==1&&muted&&fabs(gain-.4)<.00001);puts("PASS mute invalidates old completion");
 request(80,.4);assert(!muted);puts("PASS same-level SetVolume can unmute");
 reset();fail_beep=1;request(80,.4);pump(430);assert(fabs(gain-.4)<.00001);puts("PASS failed tone still applies requested volume");
 reset();request(0,.001);pump(220);assert(!muted&&count==0);pump(330);assert(muted);puts("PASS volume zero acknowledges before muting");
 return 0;
}
'''
with tempfile.TemporaryDirectory() as d:
    c=pathlib.Path(d)/'test.c'; exe=pathlib.Path(d)/'test'
    c.write_text(harness+sequence+tests)
    flags=subprocess.check_output(['pkg-config','--cflags','--libs','glib-2.0'],text=True).split()
    subprocess.run(['gcc','-Wall','-Wextra','-Wno-unused-parameter','-o',str(exe),str(c),'-pthread','-lm',*flags],check=True)
    subprocess.run([str(exe)],check=True)
