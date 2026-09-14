/**
 * RPiPlay - An open-source AirPlay mirroring server for Raspberry Pi
 * Copyright (C) 2019 Florian Draschbacher
 * Modified for:
 * UxPlay - An open-source AirPlay mirroring server
 * Copyright (C) 2021-23 F. Duncanh
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation; either version 3 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program; if not, write to the Free Software Foundation,
 * Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301  USA
 *
 * Modified for AirMix PC: per-session pipelines
 */

#include <math.h>
#include <string.h>
#include <gst/gst.h>
#include <gst/app/gstappsrc.h>
#include "audio_renderer.h"
#define SECOND_IN_NSECS 1000000000UL

static logger_t *logger = NULL;
static gchar *audiosink_pipeline = NULL;
static gchar *audio_rtp_pipeline = NULL;
static gboolean audio_sync = FALSE;
static gboolean video_sync = FALSE;
static gboolean audio_rtp = FALSE;
static gboolean aac = FALSE;
static gboolean alac = FALSE;

static void *g_loop = NULL;
static gboolean multi_session = FALSE;
static void (*error_callback)(void *cls, unsigned int sid, const char *type) = NULL;

static const gchar *avdec_aac = "avdec_aac";
static const gchar *avdec_alac = "avdec_alac";

/* GStreamer Caps strings for Airplay-defined audio compression types (ct) */

/* ct = 1; linear PCM (uncompressed): 44100/16/2, S16LE */
static const char lpcm_caps[] = "audio/x-raw,rate=(int)44100,channels=(int)2,format=S16LE,layout=interleaved";

/* ct = 2; codec_data is ALAC magic cookie:  44100/16/2 spf = 352 */
static const char alac_caps[] = "audio/x-alac,mpegversion=(int)4,channnels=(int)2,rate=(int)44100,stream-format=raw,codec_data=(buffer)"
                           "00000024""616c6163""00000000""00000160""0010280a""0e0200ff""00000000""00000000""0000ac44";

/* ct = 4; codec_data from MPEG v4 ISO 14996-3 Section 1.6.2.1:  AAC-LC 44100/2 spf = 1024 */
static const char aac_lc_caps[] = "audio/mpeg,mpegversion=(int)4,channnels=(int)2,rate=(int)44100,stream-format=raw,codec_data=(buffer)1210";

/* ct = 8; codec_data from MPEG v4 ISO 14996-3 Section 1.6.2.1: AAC_ELD 44100/2  spf = 480 */
static const char aac_eld_caps[] = "audio/mpeg,mpegversion=(int)4,channnels=(int)2,rate=(int)44100,stream-format=raw,codec_data=(buffer)f8e85000";

typedef struct audio_session_s {
    void *cls;
    unsigned int sid;
    GstElement *pipeline;
    GstElement *appsrc;
    GstElement *volume;
    GstBus *bus;
    guint bus_watch_id;
    GstClockTime base_time;
    unsigned char ct;
    gboolean render_audio;
    gboolean sync;
    struct audio_session_s *next;
} audio_session_t;

static audio_session_t *sessions = NULL;
static GMutex sessions_mutex;

static gboolean check_plugins(void) {
    GstRegistry *registry = NULL;
    const gchar *needed[] = { "app", "libav", "playback", "autodetect", "videoparsersbad", NULL };
    const gchar *gst[] = { "plugins-base", "libav", "plugins-base", "plugins-good", "plugins-bad", NULL };
    registry = gst_registry_get();
    gboolean ret = TRUE;
    for (int i = 0; i < g_strv_length((gchar **) needed); i++) {
        GstPlugin *plugin = NULL;
        plugin = gst_registry_find_plugin(registry, needed[i]);
        if (!plugin) {
            g_print("Required gstreamer plugin '%s' not found\n"
                    "Missing plugin is contained in  '[GStreamer 1.x]-%s'\n", needed[i], gst[i]);
            ret = FALSE;
            continue;
        }
        gst_object_unref(plugin);
        plugin = NULL;
    }
    if (ret == FALSE) {
        g_print("\nif the plugin is installed, but not found, your gstreamer registry may have been corrupted.\n"
                "to rebuild it when gstreamer next starts, clear your gstreamer cache with:\n"
                "\"rm -rf ~/.cache/gstreamer-1.0\"\n\n");
    }
    return ret;
}

static gboolean check_plugin_feature(const gchar *needed_feature) {
    GstPluginFeature *plugin_feature = NULL;
    GstRegistry *registry = gst_registry_get();
    gboolean ret = TRUE;

    plugin_feature = gst_registry_find_feature(registry, needed_feature, GST_TYPE_ELEMENT_FACTORY);
    if (!plugin_feature) {
        g_print("Required gstreamer libav plugin feature '%s' not found:\n\n"
                "This may be missing because the FFmpeg package used by GStreamer-1.x-libav is incomplete.\n"
                "(Some distributions provide an incomplete FFmpeg due to License or Patent issues:\n"
                "in such cases a complete version for that distribution is usually made available elsewhere)\n",
                needed_feature);
        ret = FALSE;
    } else {
        gst_object_unref(plugin_feature);
        plugin_feature = NULL;
    }
    if (ret == FALSE) {
        g_print("\nif the plugin feature is installed, but not found, your gstreamer registry may have been corrupted.\n"
                "to rebuild it when gstreamer next starts, clear your gstreamer cache with:\n"
                "\"rm -rf ~/.cache/gstreamer-1.0\"\n\n");
    }
    return ret;
}

bool gstreamer_init() {
    gst_init(NULL, NULL);
    return (bool) check_plugins();
}

static const char *format_name(unsigned char ct) {
    switch (ct) {
    case 1:
        return "PCM 44100/16/2 S16LE";
    case 2:
        return "ALAC 44100/16/2";
    case 4:
        return "AAC-LC 44100/2";
    case 8:
        return "AAC-ELD 44100/2";
    default:
        return "unknown";
    }
}

static const char *caps_for_ct(unsigned char ct) {
    switch (ct) {
    case 1:
        return lpcm_caps;
    case 2:
        return alac_caps;
    case 4:
        return aac_lc_caps;
    case 8:
        return aac_eld_caps;
    default:
        return NULL;
    }
}

/* Mirrors the historical renderer_type[]/get_renderer_type() sync choice:
 * ALAC uses the "-async" (audio_sync) setting, AAC/AAC-LC and PCM use the
 * "-vsync" (video_sync) setting (upstream naming; audio-only builds always
 * pass the same value for both). */
static gboolean determine_render(unsigned char ct, gboolean *sync_out) {
    switch (ct) {
    case 2:
        if (!alac) {
            logger_log(logger, LOGGER_INFO, "*** GStreamer libav plugin feature avdec_alac is missing, cannot decode ALAC audio");
            return FALSE;
        }
        *sync_out = audio_sync;
        return TRUE;
    case 4:
    case 8:
        if (!aac) {
            logger_log(logger, LOGGER_INFO, "*** GStreamer libav plugin feature avdec_aac is missing, cannot decode AAC audio");
            return FALSE;
        }
        *sync_out = video_sync;
        return TRUE;
    case 1:
        *sync_out = FALSE;
        return TRUE;
    default:
        return FALSE;
    }
}

static audio_session_t *find_session_locked(void *cls) {
    for (audio_session_t *session = sessions; session; session = session->next) {
        if (session->cls == cls) {
            return session;
        }
    }
    return NULL;
}

/* Holds a session's GStreamer objects (and, when discarding the session
 * outright, the session_t itself) while their release is deferred to the
 * GMainContext the bus watch dispatches in. See teardown_pipeline_locked(). */
typedef struct {
    GstBus *bus;
    GstElement *volume;
    GstElement *appsrc;
    GstElement *pipeline;
    audio_session_t *session;
} audio_session_free_ctx_t;

static gboolean audio_session_free_idle(gpointer user_data) {
    audio_session_free_ctx_t *ctx = (audio_session_free_ctx_t *) user_data;
    if (ctx->bus) {
        gst_object_unref(ctx->bus);
    }
    if (ctx->volume) {
        gst_object_unref(ctx->volume);
    }
    if (ctx->appsrc) {
        gst_object_unref(ctx->appsrc);
    }
    if (ctx->pipeline) {
        gst_object_unref(ctx->pipeline);
    }
    if (ctx->session) {
        g_free(ctx->session);
    }
    g_free(ctx);
    return G_SOURCE_REMOVE;
}

/* Stops playback and releases every GStreamer object owned by the session;
 * when free_session is non-NULL (always the same pointer as session, used
 * by callers that are discarding the session itself rather than rebuilding
 * its pipeline for a new codec) that struct is freed too. Must be called
 * with sessions_mutex held, and the caller must not release the mutex
 * between removing the session from the list (if applicable) and calling
 * this, so no other thread can observe a half-torn-down session.
 *
 * The bus watch is removed synchronously (so no further bus callback will
 * be dispatched for this session), but the actual gst_object_unref() calls
 * and the session_t free are posted via g_idle_add() to the same
 * GMainContext the bus watch used: since that context is only ever iterated
 * by the main-loop thread, this serializes the release after any bus
 * callback for this session that was already dispatching when the watch was
 * removed. If the main loop isn't running (g_loop == NULL) there is nothing
 * to serialize against, and everything is released immediately. */
static void teardown_pipeline_locked(audio_session_t *session, audio_session_t *free_session) {
    if (session->bus_watch_id) {
        g_source_remove(session->bus_watch_id);
        session->bus_watch_id = 0;
    }
    if (session->pipeline) {
        if (session->appsrc) {
            gst_app_src_end_of_stream(GST_APP_SRC(session->appsrc));
        }
        gst_element_set_state(session->pipeline, GST_STATE_NULL);
    }
    session->render_audio = FALSE;

    GstBus *bus = session->bus;
    GstElement *volume = session->volume;
    GstElement *appsrc = session->appsrc;
    GstElement *pipeline = session->pipeline;
    session->bus = NULL;
    session->volume = NULL;
    session->appsrc = NULL;
    session->pipeline = NULL;

    if (!g_loop) {
        if (bus) {
            gst_object_unref(bus);
        }
        if (volume) {
            gst_object_unref(volume);
        }
        if (appsrc) {
            gst_object_unref(appsrc);
        }
        if (pipeline) {
            gst_object_unref(pipeline);
        }
        if (free_session) {
            g_free(free_session);
        }
        return;
    }

    audio_session_free_ctx_t *ctx = g_new0(audio_session_free_ctx_t, 1);
    ctx->bus = bus;
    ctx->volume = volume;
    ctx->appsrc = appsrc;
    ctx->pipeline = pipeline;
    ctx->session = free_session;
    g_idle_add(audio_session_free_idle, ctx);
}

static gboolean gstreamer_audio_pipeline_bus_callback(GstBus *bus, GstMessage *message, gpointer user_data) {
    audio_session_t *session = (audio_session_t *) user_data;
    if (!session->pipeline) {
        /* Already stopped (destroy_session/rebuild ran on the httpd thread
         * while this callback was queued); nothing left to do. */
        return TRUE;
    }
    switch (GST_MESSAGE_TYPE(message)) {
    case GST_MESSAGE_ERROR: {
        GError *err = NULL;
        gchar *debug = NULL;
        gst_message_parse_error(message, &err, &debug);
        logger_log(logger, LOGGER_INFO, "GStreamer error (audio): %s %s", GST_MESSAGE_SRC_NAME(message), err->message);
        g_error_free(err);
        g_free(debug);
        if (session->appsrc) {
            gst_app_src_end_of_stream(GST_APP_SRC(session->appsrc));
        }
        gst_bus_set_flushing(bus, TRUE);
        if (session->pipeline) {
            gst_element_set_state(session->pipeline, GST_STATE_READY);
        }
        if (!multi_session) {
            logger_log(logger, LOGGER_INFO, "AIRMIX_EVENT error sid=%u type=sink reason=audio_error count=1", session->sid);
            if (g_loop) {
                g_main_loop_quit((GMainLoop *) g_loop);
            }
        } else if (error_callback) {
            error_callback(session->cls, session->sid, "sink");
        }
        break;
    }
    case GST_MESSAGE_EOS:
        logger_log(logger, LOGGER_INFO, "GStreamer: End-Of-Stream (audio)");
        break;
    case GST_MESSAGE_ELEMENT:
        /* many "level" messages may be sent */
        break;
    default:
        logger_log(logger, LOGGER_DEBUG, "GStreamer unhandled audio bus message: src = %s type = %s",
                   GST_MESSAGE_SRC_NAME(message), GST_MESSAGE_TYPE_NAME(message));
        break;
    }
    return TRUE;
}

/* Must be called with sessions_mutex held. Leaves the session with
 * render_audio == FALSE if ct is unsupported or its decoder is missing. */
static void build_session_pipeline(audio_session_t *session, unsigned char ct) {
    gboolean sync = FALSE;
    if (!determine_render(ct, &sync)) {
        session->ct = ct;
        session->render_audio = FALSE;
        logger_log(logger, LOGGER_ERR, "unknown or unsupported audio compression type ct = %d", ct);
        return;
    }

    GError *error = NULL;
    GString *launch = g_string_new("appsrc name=audio_source ! ");
    g_string_append(launch, "queue ! ");
    switch (ct) {
    case 4:
    case 8:
        g_string_append(launch, "avdec_aac ! ");
        break;
    case 2:
        g_string_append(launch, "avdec_alac ! ");
        break;
    default:
        break;
    }
    g_string_append(launch, "audioconvert ! ");
    g_string_append(launch, "audioresample quality=10 ! ");    /* high-quality 44.1 kHz to 48 kHz conversion */
    g_string_append(launch, "volume name=volume ! ");

    if (!audio_rtp) {
        g_string_append(launch, "level ! ");
        g_string_append(launch, audiosink_pipeline);
        g_string_append(launch, sync ? " sync=true" : " sync=false");
    } else {
        /* rtpL16pay requires S16BE (big-endian) format */
        g_string_append(launch, "audioconvert ! audio/x-raw,format=S16BE,rate=44100,channels=2 ! ");
        g_string_append(launch, "rtpL16pay ");
        g_string_append(launch, audio_rtp_pipeline);
    }

    GstElement *pipeline = gst_parse_launch(launch->str, &error);
    if (error) {
        g_error("gst_parse_launch error (audio sid=%u): %s\n", session->sid, error->message);
        g_clear_error(&error);
    }
    g_assert(pipeline);

    GstClock *clock = gst_system_clock_obtain();
    g_object_set(clock, "clock-type", GST_CLOCK_TYPE_REALTIME, NULL);
    gst_pipeline_use_clock(GST_PIPELINE_CAST(pipeline), clock);
    gst_object_unref(clock);

    GstBus *bus = gst_element_get_bus(pipeline);
    GstElement *appsrc = gst_bin_get_by_name(GST_BIN(pipeline), "audio_source");
    GstElement *volume = gst_bin_get_by_name(GST_BIN(pipeline), "volume");

    GstCaps *caps = gst_caps_from_string(caps_for_ct(ct));
    g_object_set(appsrc, "caps", caps, "stream-type", 0, "is-live", TRUE, "format", GST_FORMAT_TIME, NULL);
    gst_caps_unref(caps);

    logger_log(logger, LOGGER_DEBUG, "Audio format (sid=%u): %s", session->sid, format_name(ct));
    logger_log(logger, LOGGER_DEBUG, "GStreamer audio pipeline (sid=%u): \"%s\"", session->sid, launch->str);
    g_string_free(launch, TRUE);

    session->pipeline = pipeline;
    session->bus = bus;
    session->appsrc = appsrc;
    session->volume = volume;
    session->ct = ct;
    session->render_audio = TRUE;
    session->sync = sync;
    session->bus_watch_id = g_loop
        ? gst_bus_add_watch(bus, (GstBusFunc) gstreamer_audio_pipeline_bus_callback, session)
        : 0;

    gst_element_set_state(pipeline, GST_STATE_PLAYING);
    session->base_time = gst_element_get_base_time(appsrc);
}

void audio_renderer_init(logger_t *render_logger, const char *audiosink, const bool *audio_sync_in,
                         const bool *video_sync_in, const char *artp_pipeline) {
    logger = render_logger;
    g_free(audiosink_pipeline);
    audiosink_pipeline = g_strdup(audiosink);
    g_free(audio_rtp_pipeline);
    audio_rtp_pipeline = g_strdup(artp_pipeline);
    audio_rtp = (gboolean) strlen(artp_pipeline);
    if (audio_rtp) {
        g_print("*** Audio RTP mode enabled: sending to %s\n", artp_pipeline);
    }
    audio_sync = (gboolean) *audio_sync_in;
    video_sync = (gboolean) *video_sync_in;

    aac = check_plugin_feature(avdec_aac);
    alac = check_plugin_feature(avdec_alac);

    g_mutex_init(&sessions_mutex);
}

void audio_renderer_set_loop(void *loop) {
    g_mutex_lock(&sessions_mutex);
    g_loop = loop;
    if (g_loop) {
        /* Cheap insurance against the narrow window where a session's
         * pipeline was built (audio_get_format ran on the httpd thread)
         * before main_loop() called this: give it the bus watch it missed. */
        for (audio_session_t *session = sessions; session; session = session->next) {
            if (session->pipeline && !session->bus_watch_id) {
                session->bus_watch_id = gst_bus_add_watch(session->bus, (GstBusFunc) gstreamer_audio_pipeline_bus_callback, session);
            }
        }
    }
    g_mutex_unlock(&sessions_mutex);
}

void audio_renderer_set_multi_session(bool enabled) {
    multi_session = (gboolean) enabled;
}

void audio_renderer_set_error_callback(void (*cb)(void *cls, unsigned int sid, const char *type)) {
    error_callback = cb;
}

void audio_renderer_start(void *cls, unsigned int sid, unsigned char *ct) {
    if (!cls || !ct) {
        return;
    }
    unsigned char compression_type = *ct;

    g_mutex_lock(&sessions_mutex);
    audio_session_t *session = find_session_locked(cls);
    if (!session) {
        session = g_new0(audio_session_t, 1);
        session->cls = cls;
        session->next = sessions;
        sessions = session;
    }
    session->sid = sid;

    if (session->pipeline && (session->ct != compression_type || !session->render_audio)) {
        logger_log(logger, LOGGER_INFO, "changed audio connection (sid=%u), format %s", sid, format_name(compression_type));
        teardown_pipeline_locked(session, NULL);
    }
    if (!session->pipeline) {
        logger_log(logger, LOGGER_INFO, "start audio connection (sid=%u), format %s", sid, format_name(compression_type));
        build_session_pipeline(session, compression_type);
    }
    g_mutex_unlock(&sessions_mutex);
}

void audio_renderer_stop(void *cls) {
    g_mutex_lock(&sessions_mutex);
    if (cls) {
        audio_session_t *session = find_session_locked(cls);
        if (session && session->pipeline) {
            gst_app_src_end_of_stream(GST_APP_SRC(session->appsrc));
            gst_element_set_state(session->pipeline, GST_STATE_NULL);
            session->render_audio = FALSE;
        }
    } else {
        for (audio_session_t *session = sessions; session; session = session->next) {
            if (session->pipeline) {
                gst_app_src_end_of_stream(GST_APP_SRC(session->appsrc));
                gst_element_set_state(session->pipeline, GST_STATE_NULL);
                session->render_audio = FALSE;
            }
        }
    }
    g_mutex_unlock(&sessions_mutex);
}

void audio_renderer_destroy_session(void *cls) {
    if (!cls) {
        return;
    }
    g_mutex_lock(&sessions_mutex);
    audio_session_t *prev = NULL;
    audio_session_t *session = sessions;
    while (session && session->cls != cls) {
        prev = session;
        session = session->next;
    }
    if (!session) {
        g_mutex_unlock(&sessions_mutex);
        return;
    }
    if (prev) {
        prev->next = session->next;
    } else {
        sessions = session->next;
    }
    /* Unlink and stop stay under the same lock acquisition: no window where
     * another thread could look this session up mid-teardown. */
    teardown_pipeline_locked(session, session);
    g_mutex_unlock(&sessions_mutex);
}

void audio_renderer_render_buffer(void *cls, unsigned char *data, int *data_len, unsigned short *seqnum, uint64_t *ntp_time) {
    if (!cls || !data_len || *data_len == 0) {
        return;
    }

    g_mutex_lock(&sessions_mutex);
    audio_session_t *session = find_session_locked(cls);
    GstElement *appsrc = NULL;
    unsigned char ct = 0;
    unsigned int sid = 0;
    gboolean sync = FALSE;
    GstClockTime base_time = 0;
    if (session && session->render_audio && session->appsrc) {
        /* Referenced while still under the lock: a concurrent codec change
         * (audio_renderer_start, on the httpd thread) may teardown/rebuild
         * this session's pipeline as soon as the lock is released, and the
         * push below must not touch a freed appsrc. */
        appsrc = GST_ELEMENT(gst_object_ref(session->appsrc));
        ct = session->ct;
        sid = session->sid;
        sync = session->sync;
        base_time = session->base_time;
    }
    g_mutex_unlock(&sessions_mutex);

    if (!appsrc) {
        return;
    }

    GstClockTime pts = (GstClockTime) *ntp_time;    /* now in nsecs */
    if (sync) {
        if (pts >= base_time) {
            pts -= base_time;
        } else {
            logger_log(logger, LOGGER_ERR, "*** invalid ntp_time < base_time (sid=%u)\n%8.6f ntp_time\n%8.6f base_time",
                       sid, ((double) *ntp_time) / SECOND_IN_NSECS, ((double) base_time) / SECOND_IN_NSECS);
            gst_object_unref(appsrc);
            return;
        }
    }

    /* all audio received seems to be either ct = 8 (AAC_ELD 44100/2 spf 460 ) AirPlay Mirror protocol *
     * or ct = 2 (ALAC 44100/16/2 spf 352) AirPlay protocol.                                           *
     * first byte data[0] of ALAC frame is 0x20,                                                       *
     * first byte of AAC_ELD is 0x8c, 0x8d or 0x8e: 0x100011(00,01,10) in modern devices               *
     *                   but is 0x80, 0x81 or 0x82: 0x100000(00,01,10) in ios9, ios10 devices          *
     * first byte of AAC_LC should be 0xff (ADTS) (but has never been  seen).                          */

    GstBuffer *buffer = gst_buffer_new_allocate(NULL, *data_len, NULL);
    g_assert(buffer != NULL);
    if (sync) {
        GST_BUFFER_PTS(buffer) = pts;
    }
    gst_buffer_fill(buffer, 0, data, *data_len);
    gboolean valid = FALSE;
    switch (ct) {
    case 8: /*AAC-ELD*/
        switch (data[0]) {
        case 0x8c:
        case 0x8d:
        case 0x8e:
        case 0x80:
        case 0x81:
        case 0x82:
            valid = TRUE;
            break;
        default:
            valid = FALSE;
            break;
        }
        break;
    case 2: /*ALAC*/
        valid = (data[0] == 0x20);
        break;
    case 4:  /*AAC_LC */
        valid = (data[0] == 0xff);
        break;
    default:
        valid = TRUE;
        break;
    }
    if (valid) {
        GstFlowReturn ret = gst_app_src_push_buffer(GST_APP_SRC(appsrc), buffer);
        if (ret != GST_FLOW_OK) {
            /* Pushing into a pipeline that just moved out of PLAYING (e.g. a
             * concurrent teardown/rebuild) normally yields GST_FLOW_FLUSHING;
             * that is expected, not an error worth escalating. */
            logger_log(logger, LOGGER_DEBUG, "gst_app_src_push_buffer (sid=%u) returned %d", sid, (int) ret);
        }
    } else {
        gst_buffer_unref(buffer);
        logger_log(logger, LOGGER_ERR, "*** ERROR invalid  audio frame (compression_type %d) skipped ", ct);
        if (!multi_session) {
            logger_log(logger, LOGGER_INFO, "AIRMIX_EVENT error sid=%u type=decoder reason=audio_error count=1", sid);
        } else if (error_callback) {
            error_callback(cls, sid, "decoder");
        }
        logger_log(logger, LOGGER_ERR, "***       first byte of invalid frame was  0x%2.2x ", (unsigned int) data[0]);
    }
    gst_object_unref(appsrc);
}

void audio_renderer_set_volume(void *cls, double volume) {
    if (!cls) {
        return;
    }
    volume = (volume > 10.0) ? 10.0 : volume;
    volume = (volume < 0.0) ? 0.0 : volume;
    g_mutex_lock(&sessions_mutex);
    audio_session_t *session = find_session_locked(cls);
    if (session && session->volume) {
        g_object_set(session->volume, "volume", volume, NULL);
    }
    g_mutex_unlock(&sessions_mutex);
}

void audio_renderer_flush(void *cls) {
    (void) cls;
}

void audio_renderer_destroy(void) {
    g_mutex_lock(&sessions_mutex);
    /* Called once at process shutdown, after the main loop has already
     * stopped: there is no bus callback left to serialize against, so
     * every session can be released synchronously. */
    g_loop = NULL;
    audio_session_t *session = sessions;
    sessions = NULL;
    while (session) {
        audio_session_t *next = session->next;
        teardown_pipeline_locked(session, session);
        session = next;
    }
    g_mutex_unlock(&sessions_mutex);

    g_free(audiosink_pipeline);
    audiosink_pipeline = NULL;
    g_free(audio_rtp_pipeline);
    audio_rtp_pipeline = NULL;
}

unsigned int audio_renderer_session_count(void) {
    g_mutex_lock(&sessions_mutex);
    unsigned int count = 0;
    for (audio_session_t *session = sessions; session; session = session->next) {
        count++;
    }
    g_mutex_unlock(&sessions_mutex);
    return count;
}
