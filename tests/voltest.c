/* Does playbin keep a volume that was set before the pipeline was running? */
#include <gst/gst.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static void dump_volume_elems(GstElement *pipeline, const char *tag)
{
    GstIterator *it = gst_bin_iterate_recurse(GST_BIN(pipeline));
    GValue item = G_VALUE_INIT;
    gboolean done = FALSE;

    while (!done) {
        switch (gst_iterator_next(it, &item)) {
        case GST_ITERATOR_OK: {
            GstElement *e = g_value_get_object(&item);
            if (g_str_has_prefix(GST_ELEMENT_NAME(e), "volume") ||
                g_strcmp0(G_OBJECT_TYPE_NAME(e), "GstVolume") == 0) {
                gdouble v = -1;
                g_object_get(e, "volume", &v, NULL);
                printf("  [%s] element %-24s (%s) volume=%.4f\n",
                       tag, GST_ELEMENT_NAME(e), G_OBJECT_TYPE_NAME(e), v);
            }
            g_value_reset(&item);
            break;
        }
        case GST_ITERATOR_DONE:
            done = TRUE;
            break;
        case GST_ITERATOR_RESYNC:
            gst_iterator_resync(it);
            break;
        default:
            done = TRUE;
            break;
        }
    }
    g_value_unset(&item);
    gst_iterator_free(it);
}

static void report(GstElement *player, const char *tag)
{
    gdouble pv = -1;
    GstState st, pd;
    g_object_get(player, "volume", &pv, NULL);
    gst_element_get_state(player, &st, &pd, 0);
    printf("[%s] playbin volume=%.4f state=%s\n", tag, pv,
           gst_element_state_get_name(st));
    dump_volume_elems(player, tag);
}

int main(int argc, char **argv)
{
    GstElement *player, *sink;
    const char *uri = argc > 1 ? argv[1] : NULL;
    gdouble want = argc > 2 ? atof(argv[2]) : 0.3;

    gst_init(&argc, &argv);

    player = gst_element_factory_make("playbin", "play");
    sink = gst_element_factory_make("alsasink", "sink");
    g_object_set(sink, "device", "plughw:CARD=PCH,DEV=0", NULL);
    g_object_set(player, "audio-sink", sink, NULL);

    printf("== set volume %.3f while pipeline is NULL ==\n", want);
    g_object_set(player, "volume", want, NULL);
    report(player, "after set (NULL)");

    if (uri) {
        g_object_set(player, "uri", uri, NULL);
        printf("== starting playback ==\n");
        gst_element_set_state(player, GST_STATE_PLAYING);
        g_usleep(3000 * 1000);
        report(player, "playing 3s");

        printf("== set volume %.3f again while PLAYING ==\n", want);
        g_object_set(player, "volume", want, NULL);
        g_usleep(500 * 1000);
        report(player, "after set (PLAYING)");

        printf("== state READY then PLAYING again ==\n");
        gst_element_set_state(player, GST_STATE_READY);
        gst_element_set_state(player, GST_STATE_PLAYING);
        g_usleep(3000 * 1000);
        report(player, "after READY/PLAYING");
    }

    gst_element_set_state(player, GST_STATE_NULL);
    gst_object_unref(player);
    return 0;
}
