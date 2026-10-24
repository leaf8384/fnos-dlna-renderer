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
static double gain = .2, values[4096];
static int muted, playing = 1, transport_generation, count, beeps, fail_beep, live_beeps, max_live, canceled, kinds[4096];
static gint64 changed[4096], beep_end, begun;
static int output_set_volume(float v) { gain=v; values[count]=v; changed[count++]=g_get_monotonic_time(); return 0; }
static int output_get_volume(float *v) { *v=gain; return 0; }
static int output_get_mute(int *v) { *v=muted; return 0; }
static void set_mute_toggle(int v) {muted=v;}
static int upnp_transport_is_playing(void) {return playing;}
static int upnp_transport_generation(void) {return transport_generation;}
typedef struct {int (*valid)(void*);void (*done)(void*);void *data;} fake_beep;
static gboolean fake_done(gpointer p) {
 fake_beep *b=p; beep_end=g_get_monotonic_time();--live_beeps;if(!b->valid(b->data))++canceled;b->done(b->data);return G_SOURCE_REMOVE;
}
static void output_beep(int which,int duration,float old,int(*valid)(void*),void(*done)(void*),void *data){
 fake_beep *b=g_new0(fake_beep,1);b->valid=valid;b->done=done;b->data=data;
 assert(fabs(old-gain)<.00001);assert(duration==120);kinds[beeps++]=which;if(++live_beeps>max_live)max_live=live_beeps;
 g_timeout_add_full(G_PRIORITY_DEFAULT,fail_beep?1:duration+40,fake_done,b,g_free);
}
'''
tests = r'''
static void pump(int ms){gint64 end=g_get_monotonic_time()+ms*1000;do{while(g_main_context_iteration(NULL,FALSE)){}g_usleep(1000);}while(g_get_monotonic_time()<end);}
static void request(int level,double target){pthread_mutex_lock(&control_mutex);volume_request_locked(level,target);pthread_mutex_unlock(&control_mutex);}
static void reset(void){pthread_mutex_lock(&control_mutex);volume_cancel_locked();pthread_mutex_unlock(&control_mutex);pump(500);count=beeps=max_live=canceled=0;assert(live_beeps==0);gain=.2;muted=0;playing=1;volume_target_level_=-1;fail_beep=0;begun=g_get_monotonic_time();}
int main(void){
 reset();request(80,.4);pump(180);assert(beeps==1&&count==0);pump(300);
 assert(count==12&&fabs(gain-.4)<.00001&&changed[0]>beep_end);
 for(int i=1;i<count;i++)assert(values[i]>=values[i-1]);
 puts("PASS 120ms tone completes before one monotonic ramp");
 reset();request(80,.4);pump(100);request(30,.03);pump(600);
 assert(beeps==1&&canceled==0&&fabs(gain-.03)<.00001);
 for(int i=1;i<count;i++)assert(values[i]<=values[i-1]);
 puts("PASS requests during tone preserve it and apply latest target");
 reset();request(80,.4);pump(260);request(30,.03);pump(900);
 assert(beeps==2&&max_live==1&&fabs(gain-.03)<.00001&&volume_phase_==VOLUME_IDLE);
 puts("PASS last request during ramp receives its own final tone");
 reset();for(int i=0;i<60;i++){request(20+i,.01+i*.004);pump(50);}
 gint64 last_request=g_get_monotonic_time();pump(1000);
 assert(beeps>=5&&count>24&&changed[0]<last_request&&max_live==1&&canceled==0);
 assert(fabs(gain-(.01+59*.004))<.00001&&volume_phase_==VOLUME_IDLE);
 puts("PASS held key at 20Hz produces repeated intact tones and makes progress before release");
 reset();request(0,.001);pump(260);request(60,.1);pump(900);
 assert(!muted&&beeps==2&&fabs(gain-.1)<.00001);
 puts("PASS intermediate zero cannot mute a queued nonzero request");
 for(int phase=0;phase<3;phase++){
  reset();request(80,.4);pump(phase==0?20:phase==1?100:260);request(30,.03);
  playing=0;++transport_generation;pump(600);
  assert(fabs(gain-.03)<.00001&&volume_phase_==VOLUME_IDLE);
 }
 puts("PASS stop settles latest target during WAIT, BEEP and RAMP");
 for(int phase=0;phase<3;phase++){
  reset();request(80,.4);pump(phase==0?20:phase==1?100:260);request(30,.03);
  pthread_mutex_lock(&control_mutex);volume_cancel_locked();output_set_volume(volume_target_gain_);set_mute_toggle(1);pthread_mutex_unlock(&control_mutex);
  int before=count;pump(500);assert(count==before&&muted&&fabs(gain-.03)<.00001);
 }
 puts("PASS mute invalidates every pending stage without stale writes");
 request(30,.03);assert(!muted);puts("PASS same-level request can unmute");
 reset();fail_beep=1;request(80,.4);pump(420);assert(fabs(gain-.4)<.00001&&volume_phase_==VOLUME_IDLE);
 puts("PASS failed tone still converges");
 reset();request(80,.4);pump(100);request(100,1);pump(200);
 assert(beeps==2&&kinds[0]==0&&kinds[1]==1&&count==0);
 request(50,.1);pump(500);assert(fabs(gain-.1)<.00001&&volume_phase_==VOLUME_IDLE);
 for(int i=0;i<count;i++)assert(values[i]<=.20001);
 puts("PASS late full-scale tone re-reads target and never restores obsolete 100");
 reset();request(0,.001);pump(180);assert(!muted&&count==0);pump(300);assert(muted);
 puts("PASS final zero acknowledges before muting");
 return 0;
}
'''
with tempfile.TemporaryDirectory() as d:
    c=pathlib.Path(d)/'test.c'; exe=pathlib.Path(d)/'test'
    c.write_text(harness+sequence+tests)
    flags=subprocess.check_output(['pkg-config','--cflags','--libs','glib-2.0'],text=True).split()
    subprocess.run(['gcc','-Wall','-Wextra','-Wno-unused-parameter','-o',str(exe),str(c),'-pthread','-lm',*flags],check=True)
    subprocess.run([str(exe)],check=True)
