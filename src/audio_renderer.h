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

#ifndef AUDIO_RENDERER_H
#define AUDIO_RENDERER_H

#ifdef __cplusplus
extern "C" {
#endif

#include <stdlib.h>
#include <stdint.h>
#include <stdbool.h>
#include "../lib/logger.h"

bool gstreamer_init();

/* Configure the shared audio sink/sync settings. Does not create any
 * GStreamer pipelines: those are built lazily, per AirPlay session, the
 * first time that session calls audio_renderer_start(). */
void audio_renderer_init(logger_t *logger, const char *audiosink, const bool *audio_sync,
                         const bool *video_sync, const char *artp_pipeline);

/* Bus watches for newly-created session pipelines are attached to this
 * GMainLoop. Call once per main_loop() invocation, before any connection
 * can reach audio_renderer_start(). */
void audio_renderer_set_loop(void *loop);

/* enabled == false (single AirPlay client, the historical default): a
 * GStreamer bus error quits the main loop, exactly like upstream UxPlay.
 * enabled == true: a bus error instead calls the error callback for just
 * the affected session, leaving the other sessions and the main loop
 * running. */
void audio_renderer_set_multi_session(bool enabled);

/* Registered once, after audio_renderer_init(): called with the AirPlay
 * session's cls and sid when that session's decoder or sink fails while
 * multi-session mode is enabled. */
void audio_renderer_set_error_callback(void (*cb)(void *cls, unsigned int sid, const char *type));

/* Start (or restart, if the codec changed) the pipeline for the session
 * identified by cls. sid is the AirPlay session id used in log output. */
void audio_renderer_start(void *cls, unsigned int sid, unsigned char *ct);

/* Stop playback for the session identified by cls, or every session if
 * cls is NULL. Does not release the session's GStreamer objects: call
 * audio_renderer_destroy_session() for that. */
void audio_renderer_stop(void *cls);

/* Release the GStreamer pipeline and forget the session identified by
 * cls. Safe to call even if cls has no pipeline (or is unknown). */
void audio_renderer_destroy_session(void *cls);

void audio_renderer_render_buffer(void *cls, unsigned char *data, int *data_len,
                                  unsigned short *seqnum, uint64_t *ntp_time);

/* No-op if cls has no active session. */
void audio_renderer_set_volume(void *cls, double volume);

void audio_renderer_flush(void *cls);

/* Releases every remaining session and the shared configuration. */
void audio_renderer_destroy(void);

unsigned int audio_renderer_session_count(void);

#ifdef __cplusplus
}
#endif

#endif //AUDIO_RENDERER_H
