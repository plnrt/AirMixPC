"""
Patches the UxPlay submodule for embedded mDNS, readable audio-quality logs,
de-duplicated audio metadata output, smooth ALAC startup, and high-quality
Windows audio resampling.
Run this after cloning the submodule: python patch_cmake.py
"""

import re
import os

cmake_path = os.path.join("lib", "uxplay", "lib", "CMakeLists.txt")
uxplay_path = os.path.join("lib", "uxplay", "uxplay.cpp")
raop_rtp_path = os.path.join("lib", "uxplay", "lib", "raop_rtp.c")
raop_buffer_path = os.path.join("lib", "uxplay", "lib", "raop_buffer.c")
raop_buffer_header_path = os.path.join("lib", "uxplay", "lib", "raop_buffer.h")
audio_renderer_path = os.path.join("lib", "uxplay", "renderers", "audio_renderer.c")
raop_h_path = os.path.join("lib", "uxplay", "lib", "raop.h")
raop_c_path = os.path.join("lib", "uxplay", "lib", "raop.c")
httpd_h_path = os.path.join("lib", "uxplay", "lib", "httpd.h")
httpd_c_path = os.path.join("lib", "uxplay", "lib", "httpd.c")
raop_handlers_path = os.path.join("lib", "uxplay", "lib", "raop_handlers.h")

with open(cmake_path, "r") as f:
    content = f.read()

# 1. Add option and source file swap after aux_source_directory
old_aux = "aux_source_directory(. play_src)\nset(DIR_SRCS ${play_src})"
new_aux = """option(USE_EMBEDDED_MDNS "Use embedded mDNS responder instead of Bonjour/Avahi" OFF)

aux_source_directory(. play_src)
set(DIR_SRCS ${play_src})

# build.sh copies dnssd_embedded.c into this directory, so aux_source_directory()
# globs it whether or not the embedded responder was requested. Always drop it
# from the glob and add it back only when it is selected: otherwise a build with
# USE_EMBEDDED_MDNS=OFF compiles it alongside dnssd.c and fails to link on
# duplicate definitions of the dnssd_* API.
list(REMOVE_ITEM DIR_SRCS ./dnssd_embedded.c)

if(USE_EMBEDDED_MDNS)
  # Swap dnssd.c for the embedded mDNS implementation
  list(REMOVE_ITEM DIR_SRCS ./dnssd.c)
  list(APPEND DIR_SRCS ${CMAKE_CURRENT_SOURCE_DIR}/dnssd_embedded.c)
  add_definitions(-DUSE_EMBEDDED_MDNS)
  message(STATUS "Using embedded mDNS responder (no Bonjour/Avahi required)")
endif()"""

if 'option(USE_EMBEDDED_MDNS' not in content:
    content = content.replace(old_aux, new_aux)

# 2. Add embedded mDNS branch before the dns_sd detection block
old_dns = "#dns_sd \nif ( NOT APPLE )"
new_dns = """#dns_sd
if(USE_EMBEDDED_MDNS)
  # No external dns_sd library needed - embedded mDNS handles everything.
  # Winsock2 (ws2_32) and iphlpapi are already linked above for WIN32.
  message(STATUS "dns_sd: using embedded mDNS (no external dependency)")
elseif ( NOT APPLE )"""

# Handle both possible whitespace variants
if old_dns in content:
    content = content.replace(old_dns, new_dns)
else:
    content = content.replace("#dns_sd \r\nif ( NOT APPLE )", new_dns)
    content = content.replace("#dns_sd\nif ( NOT APPLE )", new_dns)
    content = content.replace("#dns_sd \nif ( NOT APPLE )", new_dns)

with open(cmake_path, "w") as f:
    f.write(content)

with open(uxplay_path, "r") as f:
    uxplay_content = f.read()

# 3. Add readable audio codec/resolution logging. The receiver profile in
# this fork is currently fixed at 16-bit/44.1 kHz; the log must say so rather
# than implying that an encoded ALAC bitrate was measured.
if "AIRPLAY_AUDIO_SAMPLE_RATE" not in uxplay_content:
    old_constants = "#define DEFAULT_PLAYBIN_VERSION 3\n"
    new_constants = """#define DEFAULT_PLAYBIN_VERSION 3
#define AIRPLAY_AUDIO_SAMPLE_RATE 44100U
#define AIRPLAY_AUDIO_BIT_DEPTH 16U
#define AIRPLAY_AUDIO_CHANNELS 2U
"""
    if old_constants not in uxplay_content:
        raise RuntimeError("Could not find UxPlay audio constants insertion point")
    uxplay_content = uxplay_content.replace(old_constants, new_constants, 1)

if "static const char *audio_codec_name" not in uxplay_content:
    old_audio_function = """extern \"C\" void audio_get_format (void *cls, unsigned char *ct, unsigned short *spf, bool *usingScreen, bool *isMedia, uint64_t *audioFormat) {
    unsigned char type;
    LOGI(\"ct=%d spf=%d usingScreen=%d isMedia=%d  audioFormat=0x%lx\",*ct, *spf, *usingScreen, *isMedia, (unsigned long) *audioFormat);
"""
    new_audio_function = """static const char *audio_codec_name(unsigned char ct) {
    switch (ct) {
    case 2:
        return \"ALAC\";
    case 8:
        return \"AAC-ELD\";
    case 4:
        return \"AAC-Main\";
    default:
        return \"unknown\";
    }
}

static const char *audio_quality_name(unsigned char ct, unsigned int sample_rate) {
    if (ct != 2) {
        return ct == 8 || ct == 4 ? \"Lossy\" : \"Unknown\";
    }
    return sample_rate > 48000U ? \"Hi-Res Lossless\" : \"Lossless\";
}

static void log_audio_quality(unsigned char ct, unsigned short spf, uint64_t audio_format) {
    const uint64_t pcm_bitrate_kbps =
        ((uint64_t) AIRPLAY_AUDIO_SAMPLE_RATE * AIRPLAY_AUDIO_BIT_DEPTH * AIRPLAY_AUDIO_CHANNELS + 500U) / 1000U;

    LOGI(\"audio quality: codec=%s (ct=%u); quality=%s; resolution=%u-bit/%u Hz; channels=%u; \"
         \"equivalent PCM bitrate=%\" PRIu64 \" kbps; spf=%u; audioFormat=0x%016\" PRIx64,
         audio_codec_name(ct), (unsigned int) ct,
         audio_quality_name(ct, AIRPLAY_AUDIO_SAMPLE_RATE),
         AIRPLAY_AUDIO_BIT_DEPTH, AIRPLAY_AUDIO_SAMPLE_RATE, AIRPLAY_AUDIO_CHANNELS,
         pcm_bitrate_kbps, (unsigned int) spf, audio_format);
    LOGI(\"audio quality note: AirPlay receiver profile is fixed at %u-bit/%u Hz; encoded bitrate is not exposed\",
         AIRPLAY_AUDIO_BIT_DEPTH, AIRPLAY_AUDIO_SAMPLE_RATE);
}

extern \"C\" void audio_get_format (void *cls, unsigned char *ct, unsigned short *spf, bool *usingScreen, bool *isMedia, uint64_t *audioFormat) {
    unsigned char type;
    LOGI(\"ct=%d spf=%d usingScreen=%d isMedia=%d  audioFormat=0x%lx\",*ct, *spf, *usingScreen, *isMedia, (unsigned long) *audioFormat);
    log_audio_quality(*ct, *spf, *audioFormat);
"""
    if old_audio_function not in uxplay_content:
        raise RuntimeError("Could not find UxPlay audio logging insertion point")
    uxplay_content = uxplay_content.replace(old_audio_function, new_audio_function, 1)

# 4. De-duplicate unchanged DMAP metadata blocks in the console. The iPhone
# can resend the same metadata while playback continues; keep -md file output
# unchanged, but print a block only when its text differs from the last one.
if "last_audio_metadata_text" not in uxplay_content:
    old_metadata_global = 'static std::string metadata_filename = "";\n'
    new_metadata_global = old_metadata_global + 'static std::string last_audio_metadata_text = "";\n'
    if old_metadata_global not in uxplay_content:
        raise RuntimeError("Could not find UxPlay metadata cache insertion point")
    uxplay_content = uxplay_content.replace(old_metadata_global, new_metadata_global, 1)

if "last_audio_metadata_text.clear();" not in uxplay_content:
    old_audio_reset = "    log_audio_quality(*ct, *spf, *audioFormat);\n"
    new_audio_reset = old_audio_reset + "    last_audio_metadata_text.clear();\n"
    if old_audio_reset not in uxplay_content:
        raise RuntimeError("Could not find UxPlay metadata cache reset point")
    uxplay_content = uxplay_content.replace(old_audio_reset, new_audio_reset, 1)

metadata_header = '    printf("====================Audio Metadata==================\\n");\n\n'
if metadata_header in uxplay_content:
    uxplay_content = uxplay_content.replace(metadata_header, "", 1)

if "if (!metadata_text.empty() && metadata_text != last_audio_metadata_text)" not in uxplay_content:
    old_metadata_log = '    LOGI("%s", metadata_text.c_str());\n'
    new_metadata_log = """    if (!metadata_text.empty() && metadata_text != last_audio_metadata_text) {
        printf("====================Audio Metadata==================\\n");
        LOGI("%s", metadata_text.c_str());
        last_audio_metadata_text = metadata_text;
    }
"""
    if old_metadata_log not in uxplay_content:
        raise RuntimeError("Could not find UxPlay metadata log insertion point")
    uxplay_content = uxplay_content.replace(old_metadata_log, new_metadata_log, 1)

# 5. Let the tray launcher suppress the once-per-second progress display in
# normal logs while retaining it as an opt-in troubleshooting mode.
if "static bool show_audio_progress" not in uxplay_content:
    old_progress_global = "static bool monitor_progress = false;\n"
    new_progress_global = old_progress_global + "static bool show_audio_progress = true;\n"
    if old_progress_global not in uxplay_content:
        raise RuntimeError("Could not find UxPlay progress global insertion point")
    uxplay_content = uxplay_content.replace(old_progress_global, new_progress_global, 1)

if "if (!show_audio_progress)" not in uxplay_content:
    old_display_progress = "static void display_progress(uint32_t start, uint32_t curr, uint32_t end) {\n"
    new_display_progress = old_display_progress + "    if (!show_audio_progress) return;\n"
    if old_display_progress not in uxplay_content:
        raise RuntimeError("Could not find UxPlay progress display insertion point")
    uxplay_content = uxplay_content.replace(old_display_progress, new_display_progress, 1)

if 'printf("-no-progress' not in uxplay_content:
    old_progress_help = '    printf("-nohold   Drop current connection when new client connects.\\n");\n'
    new_progress_help = (
        old_progress_help
        + '    printf("-no-progress Suppress the once-per-second audio progress display.\\n");\n'
    )
    if old_progress_help not in uxplay_content:
        raise RuntimeError("Could not find UxPlay progress help insertion point")
    uxplay_content = uxplay_content.replace(old_progress_help, new_progress_help, 1)

if 'arg == "-no-progress"' not in uxplay_content:
    old_progress_option = '        } else if (arg == "-nohold") {\n            nohold = 1;\n'
    new_progress_option = (
        '        } else if (arg == "-no-progress") {\n'
        '            show_audio_progress = false;\n'
        + old_progress_option
    )
    if old_progress_option not in uxplay_content:
        raise RuntimeError("Could not find UxPlay progress option insertion point")
    uxplay_content = uxplay_content.replace(old_progress_option, new_progress_option, 1)

# 6. Avoid accumulating ALAC frames until the first NTP sync packet and then
# burst-draining them into GStreamer. Anchor the clock to the first real ALAC
# payload and let the existing resend/reorder buffer drain at packet cadence.
# This is a narrowed backport of FDH2/UxPlay PR #548: malformed short packets
# are excluded explicitly. The resampler change is evaluated separately below
# and included after that evaluation showed a small absolute runtime cost.
if "Reset the stream-local clock mapping" not in uxplay_content:
    old_clock_reset = "    audio_type = type;\n    \n    if (use_audio) {\n"
    new_clock_reset = """    audio_type = type;
    /* Reset the stream-local clock mapping before the first frame. */
    remote_clock_offset = 0;

    if (use_audio) {
"""
    if old_clock_reset not in uxplay_content:
        raise RuntimeError("Could not find UxPlay stream clock reset insertion point")
    uxplay_content = uxplay_content.replace(old_clock_reset, new_clock_reset, 1)

# Explicit lifecycle events avoid treating unrelated control sockets as audio
# disconnects. The start event precedes quality output so a reset retains the
# format of the newly negotiated stream.
if 'fflush(stdout); /* Deliver log events to the tray pipe immediately. */' not in uxplay_content:
    anchor = '    vprintf(format, vargs);\n    printf("\\n");\n    va_end(vargs);'
    if anchor not in uxplay_content:
        raise RuntimeError("Could not find log flush insertion point")
    uxplay_content = uxplay_content.replace(
        anchor, anchor + '\n    fflush(stdout); /* Deliver log events to the tray pipe immediately. */', 1
    )

if 'struct TrayShutdownWatch' not in uxplay_content:
    anchor = '#ifdef _WIN32\nstatic gboolean handle_signal(gpointer data) {'
    replacement = '''#ifdef _WIN32
struct TrayShutdownWatch {
    HANDLE stop_event;
    HANDLE parent_process;
};

static gboolean tray_shutdown_callback(gpointer data) {
    TrayShutdownWatch *watch = (TrayShutdownWatch *) data;
    if (!watch->stop_event || !watch->parent_process ||
        WaitForSingleObject(watch->stop_event, 0) == WAIT_OBJECT_0 ||
        WaitForSingleObject(watch->parent_process, 0) == WAIT_OBJECT_0) {
        LOGI("Tray requested shutdown or exited; closing AirPlay sessions.");
        relaunch_video = false;
        g_main_loop_quit(gmainloop);
    }
    return G_SOURCE_CONTINUE;
}

static gboolean handle_signal(gpointer data) {'''
    if anchor not in uxplay_content:
        raise RuntimeError("Could not find Windows shutdown callback insertion point")
    uxplay_content = uxplay_content.replace(anchor, replacement, 1)
    anchor = '#ifdef _WIN32\n    gmainloop = loop;'
    replacement = '''#ifdef _WIN32
    gmainloop = loop;
    TrayShutdownWatch tray_watch = {NULL, NULL};
    guint tray_watch_id = 0;
    const char *tray_event = getenv("UXPLAYENHANCED_STOP_EVENT");
    const char *tray_parent = getenv("UXPLAYENHANCED_PARENT_PID");
    if (tray_event && tray_parent) {
        tray_watch.stop_event = OpenEventA(SYNCHRONIZE, FALSE, tray_event);
        tray_watch.parent_process = OpenProcess(SYNCHRONIZE, FALSE, (DWORD) strtoul(tray_parent, NULL, 10));
        tray_watch_id = g_timeout_add(100, tray_shutdown_callback, &tray_watch);
    }'''
    if anchor not in uxplay_content:
        raise RuntimeError("Could not find Windows shutdown watch insertion point")
    uxplay_content = uxplay_content.replace(anchor, replacement, 1)
    anchor = '#ifdef _WIN32\n    gmainloop = NULL;'
    replacement = '''#ifdef _WIN32
    if (tray_watch_id) g_source_remove(tray_watch_id);
    if (tray_watch.stop_event) CloseHandle(tray_watch.stop_event);
    if (tray_watch.parent_process) CloseHandle(tray_watch.parent_process);
    gmainloop = NULL;'''
    if anchor not in uxplay_content:
        raise RuntimeError("Could not find Windows shutdown watch cleanup insertion point")
    uxplay_content = uxplay_content.replace(anchor, replacement, 1)

if 'LOGI("audio session started");' not in uxplay_content:
    anchor = "    log_audio_quality(*ct, *spf, *audioFormat);\n"
    if anchor not in uxplay_content:
        raise RuntimeError("Could not find audio session start insertion point")
    uxplay_content = uxplay_content.replace(anchor, '    LOGI("audio session started");\n' + anchor, 1)

with open(uxplay_path, "w") as f:
    f.write(uxplay_content)

with open(raop_rtp_path, "r") as f:
    raop_rtp_content = f.read()

if "Start ALAC playback from the first real audio packet" not in raop_rtp_content:
    old_alac_enqueue = """            if (raop_rtp->ct == 2 && packetlen == 44)  continue;   /* ignore the ALAC packets with format information only. */

            int result = raop_buffer_enqueue(raop_rtp->buffer, packet, packetlen, 1);
"""
    new_alac_enqueue = """            if (raop_rtp->ct == 2 && packetlen == 44)  continue;   /* ignore the ALAC packets with format information only. */

            if (!raop_rtp->initial_sync && raop_rtp->ct == 2 && packetlen > 44) {
                /* Start ALAC playback from the first real audio packet instead
                 * of burst-draining frames accumulated before the first NTP sync. */
                raop_rtp->client_ntp_sync = raop_ntp_get_local_time();
                raop_rtp->rtp_sync = byteutils_get_int_be(packet, 4);
                raop_rtp->initial_sync = true;
            }

            int result = raop_buffer_enqueue(raop_rtp->buffer, packet, packetlen, 1);
"""
    if old_alac_enqueue not in raop_rtp_content:
        raise RuntimeError("Could not find UxPlay ALAC enqueue insertion point")
    raop_rtp_content = raop_rtp_content.replace(old_alac_enqueue, new_alac_enqueue, 1)

if 'LOGGER_INFO, "audio session ended"' not in raop_rtp_content:
    anchor = '    logger_log(raop_rtp->logger, LOGGER_DEBUG, "raop_rtp exiting thread");'
    if anchor not in raop_rtp_content:
        raise RuntimeError("Could not find audio session end insertion point")
    raop_rtp_content = raop_rtp_content.replace(
        anchor, anchor + '\n    logger_log(raop_rtp->logger, LOGGER_INFO, "audio session ended");', 1
    )

with open(raop_rtp_path, "w") as f:
    f.write(raop_rtp_content)

# 7. Emit machine-readable packet-health counters for the tray Auto mode.
with open(raop_buffer_header_path, "r") as f:
    buffer_header = f.read()
if "raop_buffer_stats_t" not in buffer_header:
    anchor = "typedef struct raop_buffer_s raop_buffer_t;\n"
    addition = anchor + """
typedef struct {
    uint64_t received;
    uint64_t missing;
    uint64_t late;
    uint64_t flushes;
} raop_buffer_stats_t;
"""
    buffer_header = buffer_header.replace(anchor, addition, 1)
    buffer_header = buffer_header.replace(
        "void raop_buffer_flush(raop_buffer_t *raop_buffer, int next_seq);",
        "void raop_buffer_flush(raop_buffer_t *raop_buffer, int next_seq);\n"
        "void raop_buffer_get_stats(raop_buffer_t *raop_buffer, raop_buffer_stats_t *stats);",
        1,
    )
with open(raop_buffer_header_path, "w") as f:
    f.write(buffer_header)

with open(raop_buffer_path, "r") as f:
    buffer_source = f.read()
if "raop_buffer_stats_t stats;" not in buffer_source:
    buffer_source = buffer_source.replace(
        "    raop_buffer_entry_t entries[RAOP_BUFFER_LENGTH];\n};",
        "    raop_buffer_entry_t entries[RAOP_BUFFER_LENGTH];\n    raop_buffer_stats_t stats;\n};",
        1,
    )
    buffer_source = buffer_source.replace(
        "    /* If this packet is too late, just skip it */\n"
        "    if (!raop_buffer->is_empty && seqnum_cmp(seqnum, raop_buffer->first_seqnum) < 0) {",
        "    /* If this packet is too late, just skip it */\n"
        "    if (!raop_buffer->is_empty && seqnum_cmp(seqnum, raop_buffer->first_seqnum) < 0) {\n"
        "        raop_buffer->stats.late++;",
        1,
    )
    buffer_source = buffer_source.replace(
        "    /* Update the raop_buffer entry header */",
        "    raop_buffer->stats.received++;\n\n    /* Update the raop_buffer entry header */",
        1,
    )
    buffer_source = buffer_source.replace(
        "        if (count){\n            resend_cb(opaque, raop_buffer->first_seqnum, count);",
        "        if (count){\n            raop_buffer->stats.missing += count;\n"
        "            resend_cb(opaque, raop_buffer->first_seqnum, count);",
        1,
    )
    buffer_source = buffer_source.replace(
        "void raop_buffer_flush(raop_buffer_t *raop_buffer, int next_seq) {\n    assert(raop_buffer);",
        "void raop_buffer_flush(raop_buffer_t *raop_buffer, int next_seq) {\n"
        "    assert(raop_buffer);\n    raop_buffer->stats.flushes++;",
        1,
    )
    buffer_source += """

void raop_buffer_get_stats(raop_buffer_t *raop_buffer, raop_buffer_stats_t *stats) {
    assert(raop_buffer);
    assert(stats);
    *stats = raop_buffer->stats;
}
"""
with open(raop_buffer_path, "w") as f:
    f.write(buffer_source)

with open(raop_rtp_path, "r") as f:
    raop_rtp_content = f.read()
if "airmix_retransmitted" not in raop_rtp_content:
    anchor = "    while(1) {\n"
    raop_rtp_content = raop_rtp_content.replace(
        anchor,
        "    uint64_t airmix_retransmitted = 0;\n"
        "    uint64_t airmix_last_report = 0;\n" + anchor,
        1,
    )
    raop_rtp_content = raop_rtp_content.replace(
        "                    int result = raop_buffer_enqueue(raop_rtp->buffer, resent_packet, resent_packetlen, 1);",
        "                    airmix_retransmitted++;\n"
        "                    int result = raop_buffer_enqueue(raop_rtp->buffer, resent_packet, resent_packetlen, 1);",
        1,
    )
    anchor = "            int result = raop_buffer_enqueue(raop_rtp->buffer, packet, packetlen, 1);\n            assert(result >= 0);"
    addition = anchor + """
            raop_buffer_stats_t airmix_stats;
            raop_buffer_get_stats(raop_rtp->buffer, &airmix_stats);
            if (airmix_stats.received - airmix_last_report >= 512) {
                logger_log(raop_rtp->logger, LOGGER_INFO,
                           "AIRMIX_METRIC received=%llu missing=%llu retransmitted=%llu late=%llu flushes=%llu decoder_errors=0 sink_errors=0",
                           (unsigned long long) airmix_stats.received,
                           (unsigned long long) airmix_stats.missing,
                           (unsigned long long) airmix_retransmitted,
                           (unsigned long long) airmix_stats.late,
                           (unsigned long long) airmix_stats.flushes);
                airmix_last_report = airmix_stats.received;
            }
"""
    raop_rtp_content = raop_rtp_content.replace(anchor, addition, 1)
    anchor = '    logger_log(raop_rtp->logger, LOGGER_DEBUG, "raop_rtp exiting thread");'
    final = """    raop_buffer_stats_t airmix_stats;
    raop_buffer_get_stats(raop_rtp->buffer, &airmix_stats);
    logger_log(raop_rtp->logger, LOGGER_INFO,
               "AIRMIX_METRIC received=%llu missing=%llu retransmitted=%llu late=%llu flushes=%llu decoder_errors=0 sink_errors=0",
               (unsigned long long) airmix_stats.received,
               (unsigned long long) airmix_stats.missing,
               (unsigned long long) airmix_retransmitted,
               (unsigned long long) airmix_stats.late,
               (unsigned long long) airmix_stats.flushes);
    logger_log(raop_rtp->logger, LOGGER_INFO, "AIRMIX_EVENT disconnect reason=ended");
""" + anchor
    raop_rtp_content = raop_rtp_content.replace(anchor, final, 1)
with open(raop_rtp_path, "w") as f:
    f.write(raop_rtp_content)

if 'AIRMIX_EVENT disconnect reason=network' not in uxplay_content:
    uxplay_content = uxplay_content.replace(
        'LOGI("***ERROR lost connection with client (network problem?)");',
        'LOGI("***ERROR lost connection with client (network problem?)");\n'
        '            LOGI("AIRMIX_EVENT disconnect reason=network");',
    )
    uxplay_content = uxplay_content.replace(
        'LOGI("*** ERROR lost connection with client (network problem?)");',
        'LOGI("*** ERROR lost connection with client (network problem?)");\n'
        '        LOGI("AIRMIX_EVENT disconnect reason=network");',
    )
    with open(uxplay_path, "w") as f:
        f.write(uxplay_content)

# 8. renderers/audio_renderer.c is no longer string-patched: build.sh copies
# AirMix PC's own src/audio_renderer.c/.h (per-session pipelines, D4) over the
# upstream file before this script runs. Fail loudly if that copy is missing,
# rather than silently keeping a stale single-session renderer.
with open(audio_renderer_path, "r") as f:
    audio_renderer_content = f.read()

if "audio_renderer_session_count" not in audio_renderer_content:
    raise RuntimeError("audio_renderer.c replacement missing; run build.sh")

# 9. Multi-session admission control: per-connection session ids, a
# connection-count admission check keyed off raop->max_raop_clients, and an
# address-scoped disconnect API so one AirPlay session can be dropped without
# tearing down the others. Default max_raop_clients = 1 keeps single-client
# behavior identical to upstream.
with open(raop_h_path, "r") as f:
    raop_h_content = f.read()

if "unsigned int session_id;" not in raop_h_content:
    anchor = "struct raop_callbacks_s {\n    void* cls;\n"
    if anchor not in raop_h_content:
        raise RuntimeError("raop.h: could not find raop_callbacks_s cls field")
    raop_h_content = raop_h_content.replace(anchor, anchor + "    unsigned int session_id;\n", 1)

if "raop_connection_id" not in raop_h_content:
    anchor = "RAOP_API void raop_destroy(raop_t *raop);\n"
    if anchor not in raop_h_content:
        raise RuntimeError("raop.h: could not find raop_destroy declaration")
    raop_h_content = raop_h_content.replace(
        anchor,
        anchor
        + "RAOP_API unsigned int raop_connection_id(void *connection);\n"
        "RAOP_API void raop_disconnect_connection(raop_t *raop, void *connection);\n"
        "RAOP_API void raop_set_max_clients(raop_t *raop, unsigned int max_clients);\n",
        1,
    )

with open(raop_h_path, "w") as f:
    f.write(raop_h_content)

with open(raop_c_path, "r") as f:
    raop_c_content = f.read()

if "unsigned int session_counter;" not in raop_c_content:
    anchor = "  /* used for setting HLS video language choices */\n    char *lang;\n};"
    if anchor not in raop_c_content:
        raise RuntimeError("raop.c: could not find end of raop_s struct")
    raop_c_content = raop_c_content.replace(
        anchor,
        "  /* used for setting HLS video language choices */\n    char *lang;\n\n"
        "    /* multi-session admission control (AirMix PC) */\n"
        "    unsigned int session_counter;\n"
        "    unsigned int max_raop_clients;\n};",
        1,
    )

if "unsigned int session_id;" not in raop_c_content:
    anchor = "    unsigned int zone_id;\n\n    connection_type_t connection_type; \n"
    if anchor not in raop_c_content:
        raise RuntimeError("raop.c: could not find raop_conn_s zone_id field")
    raop_c_content = raop_c_content.replace(
        anchor,
        "    unsigned int zone_id;\n    unsigned int session_id;\n\n    connection_type_t connection_type; \n",
        1,
    )

if "conn->session_id = ++raop->session_counter;" not in raop_c_content:
    anchor = "    conn->zone_id = zone_id;\n"
    if anchor not in raop_c_content:
        raise RuntimeError("raop.c: could not find conn_init zone_id assignment")
    raop_c_content = raop_c_content.replace(
        anchor, anchor + "    conn->session_id = ++raop->session_counter;\n", 1
    )
    anchor = (
        "    if (raop->callbacks.conn_init) {\n"
        "        raop->callbacks.conn_init(raop->callbacks.cls);\n"
        "    }"
    )
    if anchor not in raop_c_content:
        raise RuntimeError("raop.c: could not find conn_init callback invocation")
    raop_c_content = raop_c_content.replace(
        anchor,
        "    if (raop->callbacks.conn_init) {\n        raop->callbacks.conn_init(conn);\n    }",
        1,
    )

if "raop->callbacks.conn_destroy(conn);" not in raop_c_content:
    old_conn_destroy = """    if (raop->callbacks.conn_destroy) {
        raop->callbacks.conn_destroy(raop->callbacks.cls);
    }

    if (conn->raop_rtp) {
        /* This is done in case TEARDOWN was not called */
        raop_rtp_destroy(conn->raop_rtp);
    }
    if (conn->raop_rtp_mirror) {
        /* This is done in case TEARDOWN was not called */
        raop_rtp_mirror_destroy(conn->raop_rtp_mirror);
    }
    if (conn->raop_ntp) {
        raop_ntp_destroy(conn->raop_ntp);
    }

    if (raop->callbacks.video_flush) {
        raop->callbacks.video_flush(raop->callbacks.cls);
    }
"""
    new_conn_destroy = """    if (conn->raop_rtp) {
        /* This is done in case TEARDOWN was not called */
        raop_rtp_destroy(conn->raop_rtp);
    }
    if (conn->raop_rtp_mirror) {
        /* This is done in case TEARDOWN was not called */
        raop_rtp_mirror_destroy(conn->raop_rtp_mirror);
    }
    if (conn->raop_ntp) {
        raop_ntp_destroy(conn->raop_ntp);
    }

    if (raop->callbacks.conn_destroy) {
        raop->callbacks.conn_destroy(conn);
    }

    if (raop->callbacks.video_flush) {
        raop->callbacks.video_flush(raop->callbacks.cls);
    }
"""
    if old_conn_destroy not in raop_c_content:
        raise RuntimeError("raop.c: could not find conn_destroy teardown sequence")
    raop_c_content = raop_c_content.replace(old_conn_destroy, new_conn_destroy, 1)

if "raop_connection_count >= (int) raop->max_raop_clients" not in raop_c_content:
    old_admission = "            if (httpd_count_connection_type(raop->httpd, CONNECTION_TYPE_RAOP)) {"
    new_admission = (
        "            int raop_connection_count = httpd_count_connection_type(raop->httpd, CONNECTION_TYPE_RAOP);\n"
        "            if (raop_connection_count >= (int) raop->max_raop_clients) {"
    )
    if old_admission not in raop_c_content:
        raise RuntimeError("raop.c: could not find RAOP admission check")
    raop_c_content = raop_c_content.replace(old_admission, new_admission, 1)

if "raop->max_raop_clients = 1;" not in raop_c_content:
    anchor = "    raop->hls_support = false;\n    raop->hls_pending = false;\n    \n    raop->nonce = NULL;"
    if anchor not in raop_c_content:
        raise RuntimeError("raop.c: could not find raop_init hls_pending initialization")
    raop_c_content = raop_c_content.replace(
        anchor,
        "    raop->hls_support = false;\n    raop->hls_pending = false;\n\n"
        "    raop->session_counter = 0;\n    raop->max_raop_clients = 1;\n    \n    raop->nonce = NULL;",
        1,
    )

if "raop_connection_id(void *connection)" not in raop_c_content:
    anchor = 'uint64_t get_local_time() {\n    return raop_ntp_get_local_time();\n}'
    if anchor not in raop_c_content:
        raise RuntimeError("raop.c: could not find get_local_time definition")
    raop_c_content = raop_c_content.replace(
        anchor,
        """unsigned int raop_connection_id(void *connection) {
    raop_conn_t *conn = (raop_conn_t *) connection;
    if (!conn) {
        return 0;
    }
    return conn->session_id;
}

void raop_disconnect_connection(raop_t *raop, void *connection) {
    if (!raop || !connection) {
        return;
    }
    httpd_remove_connection_by_user_data(raop->httpd, connection);
}

void raop_set_max_clients(raop_t *raop, unsigned int max_clients) {
    assert(raop);
    raop->max_raop_clients = (max_clients > 0) ? max_clients : 1;
}

"""
        + anchor,
        1,
    )

with open(raop_c_path, "w") as f:
    f.write(raop_c_content)

with open(httpd_h_path, "r") as f:
    httpd_h_content = f.read()
if "httpd_remove_connection_by_user_data" not in httpd_h_content:
    anchor = "void httpd_remove_connections_by_type(httpd_t *httpd, connection_type_t type);\n"
    if anchor not in httpd_h_content:
        raise RuntimeError("httpd.h: could not find httpd_remove_connections_by_type declaration")
    httpd_h_content = httpd_h_content.replace(
        anchor, anchor + "void httpd_remove_connection_by_user_data(httpd_t *httpd, void *user_data);\n", 1
    )
with open(httpd_h_path, "w") as f:
    f.write(httpd_h_content)

with open(httpd_c_path, "r") as f:
    httpd_c_content = f.read()
if "httpd_remove_connection_by_user_data" not in httpd_c_content:
    anchor = """void
httpd_remove_connections_by_type(httpd_t *httpd, connection_type_t type) {
    for (int i = 0; i < httpd->max_connections; i++) {
        http_connection_t *connection = &httpd->connections[i];
        if (!connection->connected || connection->type != type) {
            continue;
        }
        connection->pending_remove = 1;
    }
}
"""
    if anchor not in httpd_c_content:
        raise RuntimeError("httpd.c: could not find httpd_remove_connections_by_type")
    httpd_c_content = httpd_c_content.replace(
        anchor,
        anchor
        + """
void
httpd_remove_connection_by_user_data(httpd_t *httpd, void *user_data) {
    for (int i = 0; i < httpd->max_connections; i++) {
        http_connection_t *connection = &httpd->connections[i];
        if (!connection->connected || connection->user_data != user_data) {
            continue;
        }
        connection->pending_remove = 1;
    }
}
""",
        1,
    )
with open(httpd_c_path, "w") as f:
    f.write(httpd_c_content)

with open(raop_handlers_path, "r") as f:
    raop_handlers_content = f.read()

if "raop_callbacks_t conn_cbs = raop->callbacks;" not in raop_handlers_content:
    old_init_calls = """        conn->raop_ntp = raop_ntp_init(raop->logger, &raop->callbacks, remote,
                                       conn->remotelen, (unsigned short) timing_rport, &time_protocol);
        raop_ntp_start(conn->raop_ntp, &timing_lport);
        conn->raop_rtp = raop_rtp_init(raop->logger, &raop->callbacks, conn->raop_ntp,
                                       remote, conn->remotelen, aeskey, aesiv);
        conn->raop_rtp_mirror = raop_rtp_mirror_init(raop->logger, &raop->callbacks,
                                                     conn->raop_ntp, remote, conn->remotelen, aeskey);
"""
    new_init_calls = """        raop_callbacks_t conn_cbs = raop->callbacks;
        conn_cbs.cls = conn;
        conn_cbs.session_id = conn->session_id;
        conn->raop_ntp = raop_ntp_init(raop->logger, &conn_cbs, remote,
                                       conn->remotelen, (unsigned short) timing_rport, &time_protocol);
        raop_ntp_start(conn->raop_ntp, &timing_lport);
        conn->raop_rtp = raop_rtp_init(raop->logger, &conn_cbs, conn->raop_ntp,
                                       remote, conn->remotelen, aeskey, aesiv);
        conn->raop_rtp_mirror = raop_rtp_mirror_init(raop->logger, &conn_cbs,
                                                     conn->raop_ntp, remote, conn->remotelen, aeskey);
"""
    if old_init_calls not in raop_handlers_content:
        raise RuntimeError("raop_handlers.h: could not find raop_ntp/raop_rtp/raop_rtp_mirror init calls")
    raop_handlers_content = raop_handlers_content.replace(old_init_calls, new_init_calls, 1)

if "raop->callbacks.report_client_request(conn," not in raop_handlers_content:
    old = "raop->callbacks.report_client_request(raop->callbacks.cls, deviceID, model, name, &admit_client);"
    new = "raop->callbacks.report_client_request(conn, deviceID, model, name, &admit_client);"
    if old not in raop_handlers_content:
        raise RuntimeError("raop_handlers.h: could not find report_client_request call")
    raop_handlers_content = raop_handlers_content.replace(old, new, 1)

if "raop->callbacks.audio_get_format(conn," not in raop_handlers_content:
    old = "raop->callbacks.audio_get_format(raop->callbacks.cls, &ct, &spf, &usingScreen, &isMedia, &audioFormat);"
    new = "raop->callbacks.audio_get_format(conn, &ct, &spf, &usingScreen, &isMedia, &audioFormat);"
    if old not in raop_handlers_content:
        raise RuntimeError("raop_handlers.h: could not find audio_get_format call")
    raop_handlers_content = raop_handlers_content.replace(old, new, 1)

if "raop->callbacks.conn_feedback(conn);" not in raop_handlers_content:
    old = "raop->callbacks.conn_feedback(raop->callbacks.cls);"
    new = "raop->callbacks.conn_feedback(conn);"
    if old not in raop_handlers_content:
        raise RuntimeError("raop_handlers.h: could not find conn_feedback call")
    raop_handlers_content = raop_handlers_content.replace(old, new, 1)

if "raop->callbacks.audio_stop_coverart_rendering(conn);" not in raop_handlers_content:
    old = "raop->callbacks.audio_stop_coverart_rendering(raop->callbacks.cls);"
    new = "raop->callbacks.audio_stop_coverart_rendering(conn);"
    if old not in raop_handlers_content:
        raise RuntimeError("raop_handlers.h: could not find audio_stop_coverart_rendering call")
    raop_handlers_content = raop_handlers_content.replace(old, new, 1)

with open(raop_handlers_path, "w") as f:
    f.write(raop_handlers_content)

# Move the raop_rtp_thread_udp tail's "running = false" transition to after
# the final AIRMIX_METRIC/disconnect/session-ended log lines, so those logs
# (and a future sid tag) are emitted while the connection is still considered
# running, matching the pre-teardown state raop_disconnect_connection expects.
with open(raop_rtp_path, "r") as f:
    raop_rtp_content = f.read()

if 'audio session ended");\n\n    // Ensure running reflects the actual state' not in raop_rtp_content:
    old_tail = """    // Ensure running reflects the actual state
    MUTEX_LOCK(raop_rtp->run_mutex);
    raop_rtp->running = false;
    MUTEX_UNLOCK(raop_rtp->run_mutex);

    raop_buffer_stats_t airmix_stats;
    raop_buffer_get_stats(raop_rtp->buffer, &airmix_stats);
    logger_log(raop_rtp->logger, LOGGER_INFO,
               "AIRMIX_METRIC received=%llu missing=%llu retransmitted=%llu late=%llu flushes=%llu decoder_errors=0 sink_errors=0",
               (unsigned long long) airmix_stats.received,
               (unsigned long long) airmix_stats.missing,
               (unsigned long long) airmix_retransmitted,
               (unsigned long long) airmix_stats.late,
               (unsigned long long) airmix_stats.flushes);
    logger_log(raop_rtp->logger, LOGGER_INFO, "AIRMIX_EVENT disconnect reason=ended");
    logger_log(raop_rtp->logger, LOGGER_DEBUG, "raop_rtp exiting thread");
    logger_log(raop_rtp->logger, LOGGER_INFO, "audio session ended");

    return 0;
}"""
    new_tail = """    raop_buffer_stats_t airmix_stats;
    raop_buffer_get_stats(raop_rtp->buffer, &airmix_stats);
    logger_log(raop_rtp->logger, LOGGER_INFO,
               "AIRMIX_METRIC received=%llu missing=%llu retransmitted=%llu late=%llu flushes=%llu decoder_errors=0 sink_errors=0",
               (unsigned long long) airmix_stats.received,
               (unsigned long long) airmix_stats.missing,
               (unsigned long long) airmix_retransmitted,
               (unsigned long long) airmix_stats.late,
               (unsigned long long) airmix_stats.flushes);
    logger_log(raop_rtp->logger, LOGGER_INFO, "AIRMIX_EVENT disconnect reason=ended");
    logger_log(raop_rtp->logger, LOGGER_DEBUG, "raop_rtp exiting thread");
    logger_log(raop_rtp->logger, LOGGER_INFO, "audio session ended");

    // Ensure running reflects the actual state
    MUTEX_LOCK(raop_rtp->run_mutex);
    raop_rtp->running = false;
    MUTEX_UNLOCK(raop_rtp->run_mutex);

    return 0;
}"""
    if old_tail not in raop_rtp_content:
        raise RuntimeError("raop_rtp.c: could not find raop_rtp_thread_udp tail to reorder")
    raop_rtp_content = raop_rtp_content.replace(old_tail, new_tail, 1)

with open(raop_rtp_path, "w") as f:
    f.write(raop_rtp_content)

# uxplay.cpp: -maxclients option, audio-only admission clamp, and wiring the
# parsed limit into raop via raop_set_max_clients before the httpd starts.
with open(uxplay_path, "r") as f:
    uxplay_content = f.read()

if "static unsigned int max_clients = 1;" not in uxplay_content:
    anchor = "static int nohold = 0;\n"
    if anchor not in uxplay_content:
        raise RuntimeError("uxplay.cpp: could not find nohold global")
    uxplay_content = uxplay_content.replace(anchor, anchor + "static unsigned int max_clients = 1;\n", 1)

if 'printf("-maxclients' not in uxplay_content:
    anchor = 'printf("-no-progress Suppress the once-per-second audio progress display.\\n");\n'
    if anchor not in uxplay_content:
        raise RuntimeError("uxplay.cpp: could not find -no-progress help insertion point")
    uxplay_content = uxplay_content.replace(
        anchor,
        anchor
        + '    printf("-maxclients n Allow up to n simultaneous AirPlay audio clients"\n'
        '           " (default 1, max 12; audio-only, forced to 1 if video is enabled)\\n");\n',
        1,
    )

if 'arg == "-maxclients"' not in uxplay_content:
    anchor = '        } else if (arg == "-nohold") {\n            nohold = 1;\n'
    if anchor not in uxplay_content:
        raise RuntimeError("uxplay.cpp: could not find -nohold option parsing")
    uxplay_content = uxplay_content.replace(
        anchor,
        anchor
        + '        } else if (arg == "-maxclients") {\n'
        '            unsigned int n = 12;\n'
        '            if (!get_value(argv[++i], &n)) {\n'
        '                fprintf(stderr, "invalid \\"-maxclients %s\\"; -maxclients n : range [1,12]\\n", argv[i]);\n'
        '                exit(1);\n'
        '            }\n'
        '            max_clients = n;\n',
        1,
    )

if "-maxclients is audio-only" not in uxplay_content:
    anchor = """    if (videosink == "0") {
        use_video = false;
	videosink.erase();
        videosink.append("fakesink");
	videosink_options.erase();
	LOGI("video_disabled");
        display[3] = 1; /* set fps to 1 frame per sec when no video will be shown */
    }
"""
    if anchor not in uxplay_content:
        raise RuntimeError("uxplay.cpp: could not find use_video assignment to anchor the audio-only admission clamp")
    uxplay_content = uxplay_content.replace(
        anchor,
        anchor
        + """
    if (max_clients > 1 && use_video) {
        LOGI("-maxclients is audio-only (-vs 0); forcing 1");
        max_clients = 1;
    }
    if (max_clients < 1) {
        max_clients = 1;
    } else if (max_clients > 12) {
        max_clients = 12;
    }
""",
        1,
    )

if "raop_set_max_clients(raop, max_clients);" not in uxplay_content:
    anchor = "    raop_set_log_callback(raop, log_callback, NULL);\n    raop_set_log_level(raop, log_level);\n"
    if anchor not in uxplay_content:
        raise RuntimeError("uxplay.cpp: could not find raop_set_log_level call in start_raop_server")
    uxplay_content = uxplay_content.replace(
        anchor, anchor + "    raop_set_max_clients(raop, max_clients);\n", 1
    )

with open(uxplay_path, "w") as f:
    f.write(uxplay_content)

# 10. Per-session audio renderer routing (D4/D5): a fixed slot table keyed by
# the opaque raop connection pointer (the "cls" every raop callback already
# carries) tracks per-session clock offset, feedback timeouts, and device
# info, and every audio_renderer_* call site is threaded with cls so each
# AirPlay session gets its own GStreamer pipeline.
with open(uxplay_path, "r") as f:
    uxplay_content = f.read()

if "airmix_slot_claim" not in uxplay_content:
    old_conn = """extern "C" void conn_init (void *cls) {
    open_connections++;
    LOGD("Open connections: %i", open_connections);
    //video_renderer_update_background(1);
}

extern "C" void conn_destroy (void *cls) {
    //video_renderer_update_background(-1);
    open_connections--;
    LOGD("Open connections: %i", open_connections);
    if (open_connections == 0) {
        remote_clock_offset = 0;
        if (use_audio) {
            audio_renderer_stop();
        }
        if (dacpfile.length()) {
            remove (dacpfile.c_str());
        }
        if (mux_to_file) {
            mux_renderer_stop();
        }
    }
}
"""
    new_conn = """#define AIRMIX_MAX_SLOTS 12   /* matches httpd.c's MAX_CONNECTIONS */
struct airmix_slot {
    void *cls;
    unsigned int sid;
    uint64_t clock_offset;
    unsigned int missed_feedback;
    bool audio_started;
    std::string device;
    std::string model;
};
static airmix_slot airmix_slots[AIRMIX_MAX_SLOTS];

static airmix_slot *airmix_slot_find(void *cls) {
    if (!cls) return NULL;
    for (int i = 0; i < AIRMIX_MAX_SLOTS; i++) {
        if (airmix_slots[i].cls == cls) return &airmix_slots[i];
    }
    return NULL;
}

static airmix_slot *airmix_slot_claim(void *cls) {
    if (!cls) return NULL;
    airmix_slot *slot = airmix_slot_find(cls);
    if (slot) return slot;
    for (int i = 0; i < AIRMIX_MAX_SLOTS; i++) {
        if (airmix_slots[i].cls == NULL) {
            airmix_slots[i] = airmix_slot();
            airmix_slots[i].cls = cls;
            airmix_slots[i].sid = raop_connection_id(cls);
            return &airmix_slots[i];
        }
    }
    return NULL;
}

static void airmix_slot_release(void *cls) {
    airmix_slot *slot = airmix_slot_find(cls);
    if (slot) {
        *slot = airmix_slot();
    }
}

extern "C" void conn_init (void *cls) {
    open_connections++;
    LOGD("Open connections: %i", open_connections);
    airmix_slot_claim(cls);
    //video_renderer_update_background(1);
}

extern "C" void conn_destroy (void *cls) {
    //video_renderer_update_background(-1);
    open_connections--;
    LOGD("Open connections: %i", open_connections);
    if (use_audio) {
        audio_renderer_stop(cls);
        audio_renderer_destroy_session(cls);
    }
    airmix_slot_release(cls);
    if (open_connections == 0) {
        remote_clock_offset = 0;
        if (use_audio) {
            audio_renderer_stop(NULL);
        }
        if (dacpfile.length()) {
            remove (dacpfile.c_str());
        }
        if (mux_to_file) {
            mux_renderer_stop();
        }
    }
}
"""
    if old_conn not in uxplay_content:
        raise RuntimeError("uxplay.cpp: could not find conn_init/conn_destroy to add the AirMix slot table")
    uxplay_content = uxplay_content.replace(old_conn, new_conn, 1)

if "airmix_slot *slot = airmix_slot_find(cls);\n        uint64_t clock_offset" not in uxplay_content:
    old_audio_process = """    if (use_audio) {
        if (!remote_clock_offset) {
            uint64_t local_time = (data->ntp_time_local ? data->ntp_time_local : get_local_time());
            remote_clock_offset = local_time - data->ntp_time_remote;
        }
        data->ntp_time_remote = data->ntp_time_remote + remote_clock_offset;
        switch (data->ct) {
"""
    new_audio_process = """    if (use_audio) {
        airmix_slot *slot = airmix_slot_find(cls);
        uint64_t clock_offset = slot ? slot->clock_offset : 0;
        if (!clock_offset) {
            uint64_t local_time = (data->ntp_time_local ? data->ntp_time_local : get_local_time());
            clock_offset = local_time - data->ntp_time_remote;
            if (slot) slot->clock_offset = clock_offset;
        }
        data->ntp_time_remote = data->ntp_time_remote + clock_offset;
        switch (data->ct) {
"""
    if old_audio_process not in uxplay_content:
        raise RuntimeError("uxplay.cpp: could not find audio_process clock-offset logic")
    uxplay_content = uxplay_content.replace(old_audio_process, new_audio_process, 1)

if "audio_renderer_render_buffer(cls," not in uxplay_content:
    old = "        audio_renderer_render_buffer(data->data, &(data->data_len), &(data->seqnum), &(data->ntp_time_remote));"
    new = "        audio_renderer_render_buffer(cls, data->data, &(data->data_len), &(data->seqnum), &(data->ntp_time_remote));"
    if old not in uxplay_content:
        raise RuntimeError("uxplay.cpp: could not find audio_renderer_render_buffer call")
    uxplay_content = uxplay_content.replace(old, new, 1)

if "audio_renderer_flush(cls);" not in uxplay_content:
    old = """extern "C" void audio_flush (void *cls) {
    if (use_audio) {
        audio_renderer_flush();
    }
}"""
    new = """extern "C" void audio_flush (void *cls) {
    if (use_audio) {
        audio_renderer_flush(cls);
    }
}"""
    if old not in uxplay_content:
        raise RuntimeError("uxplay.cpp: could not find audio_flush callback")
    uxplay_content = uxplay_content.replace(old, new, 1)

if "audio_renderer_set_volume(cls, gst_volume);" not in uxplay_content:
    old = "    audio_renderer_set_volume(gst_volume);"
    new = "    audio_renderer_set_volume(cls, gst_volume);"
    if old not in uxplay_content:
        raise RuntimeError("uxplay.cpp: could not find audio_renderer_set_volume call")
    uxplay_content = uxplay_content.replace(old, new, 1)

if "airmix_slot_find(cls)) {\n        slot->clock_offset = 0;" not in uxplay_content:
    old_get_format = """    audio_type = type;
    /* Reset the stream-local clock mapping before the first frame. */
    remote_clock_offset = 0;

    if (use_audio) {
      audio_renderer_start(ct);
    }
"""
    new_get_format = """    audio_type = type;
    /* Reset the stream-local clock mapping before the first frame. */
    if (airmix_slot *slot = airmix_slot_find(cls)) {
        slot->clock_offset = 0;
        slot->audio_started = true;
    }

    if (use_audio) {
      audio_renderer_start(cls, raop_connection_id(cls), ct);
    }
"""
    if old_get_format not in uxplay_content:
        raise RuntimeError("uxplay.cpp: could not find audio_get_format renderer-start logic")
    uxplay_content = uxplay_content.replace(old_get_format, new_get_format, 1)

if "audio_renderer_set_multi_session(max_clients > 1);" not in uxplay_content:
    anchor = """    if (raop_init2(raop, nohold, mac_address.c_str(), keyfile.c_str())){
        LOGE("Error initializing raop (2)!");
        free (raop);
        return -1;
    }
"""
    if anchor not in uxplay_content:
        raise RuntimeError("uxplay.cpp: could not find raop_init2 call in start_raop_server")
    uxplay_content = uxplay_content.replace(
        anchor, anchor + "    audio_renderer_set_multi_session(max_clients > 1);\n", 1
    )

if "audio_renderer_set_loop((void *) loop);" not in uxplay_content:
    old_main_loop_top = """#define MAX_VIDEO_RENDERERS 3
#define MAX_AUDIO_RENDERERS 2
static void main_loop()  {
    guint gst_video_bus_watch_id[MAX_VIDEO_RENDERERS] = { 0 };
    guint gst_audio_bus_watch_id[MAX_AUDIO_RENDERERS] = { 0 };
    GMainLoop *loop = g_main_loop_new(NULL,FALSE);
"""
    new_main_loop_top = """#define MAX_VIDEO_RENDERERS 3
static void main_loop()  {
    guint gst_video_bus_watch_id[MAX_VIDEO_RENDERERS] = { 0 };
    GMainLoop *loop = g_main_loop_new(NULL,FALSE);
"""
    if old_main_loop_top not in uxplay_content:
        raise RuntimeError("uxplay.cpp: could not find main_loop's audio/video bus watch declarations")
    uxplay_content = uxplay_content.replace(old_main_loop_top, new_main_loop_top, 1)

    old_n_reset = "    n_video_renderers = 0;\n    n_audio_renderers = 0;\n"
    new_n_reset = "    n_video_renderers = 0;\n"
    if old_n_reset not in uxplay_content:
        raise RuntimeError("uxplay.cpp: could not find main_loop's renderer-count reset")
    uxplay_content = uxplay_content.replace(old_n_reset, new_n_reset, 1)

    old_audio_listen = """    if (use_audio) {
        rtptime_start = 0;
        rtptime_end = 0;
        monitor_progress = true;
        artist.erase();
        coverart_artist.erase();
        progress_id  = g_timeout_add_seconds(1,(GSourceFunc) progress_callback, (gpointer) loop);
        n_audio_renderers = 2;
        g_assert(n_audio_renderers <= MAX_AUDIO_RENDERERS);
        for (int i = 0; i < n_audio_renderers; i++) {
            gst_audio_bus_watch_id[i] = (guint) audio_renderer_listen((void *)loop, i);      
        }
    }
"""
    new_audio_listen = """    if (use_audio) {
        rtptime_start = 0;
        rtptime_end = 0;
        monitor_progress = true;
        artist.erase();
        coverart_artist.erase();
        progress_id  = g_timeout_add_seconds(1,(GSourceFunc) progress_callback, (gpointer) loop);
        audio_renderer_set_loop((void *) loop);
    }
"""
    if old_audio_listen not in uxplay_content:
        raise RuntimeError("uxplay.cpp: could not find main_loop's audio bus-watch setup")
    uxplay_content = uxplay_content.replace(old_audio_listen, new_audio_listen, 1)

    old_audio_cleanup = """    for (int i = 0; i < n_audio_renderers; i++) {
        if (gst_audio_bus_watch_id[i] > 0) g_source_remove(gst_audio_bus_watch_id[i]);
    }
"""
    if old_audio_cleanup not in uxplay_content:
        raise RuntimeError("uxplay.cpp: could not find main_loop's audio bus-watch cleanup")
    uxplay_content = uxplay_content.replace(old_audio_cleanup, "", 1)

    old_global = "static int n_audio_renderers = 0;\n"
    if old_global not in uxplay_content:
        raise RuntimeError("uxplay.cpp: could not find n_audio_renderers global declaration")
    uxplay_content = uxplay_content.replace(old_global, "", 1)

if "audio_renderer_stop(NULL);\n        }\n        if (use_video && (close_window" not in uxplay_content:
    old = """        if (use_audio) {
            audio_renderer_stop();
        }
        if (use_video && (close_window || preserve_connections || full_video_reset)) {
"""
    new = """        if (use_audio) {
            audio_renderer_stop(NULL);
        }
        if (use_video && (close_window || preserve_connections || full_video_reset)) {
"""
    if old not in uxplay_content:
        raise RuntimeError("uxplay.cpp: could not find the relaunch_video audio_renderer_stop() call")
    uxplay_content = uxplay_content.replace(old, new, 1)

with open(uxplay_path, "w") as f:
    f.write(uxplay_content)

print(f"Patched {cmake_path}")
