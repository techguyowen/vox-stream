#pragma once

#include <string>
#include <thread>
#include <atomic>
#include <mutex>
#include <condition_variable>
#include "audio_resampler.h"
#include "censor_engine.h"
#include "caption_output.h"

namespace ObsCaptions {

enum class SttEngineType {
    GoogleCloud,
    GeminiLive,
    LocalBackend
};

struct SttClientConfig {
    SttEngineType engine_type = SttEngineType::GoogleCloud;
    std::string api_key;
    std::string credentials_path;
    std::string backend_url = "http://127.0.0.1:8765";
    float noise_gate_db = -45.0f;
    float vad_sensitivity = 0.5f;
};

class SttWorkerClient {
public:
    SttWorkerClient(AudioRingBuffer &ring_buffer, CensorEngine &censor, CaptionOutputSink &sink);
    ~SttWorkerClient();

    void setConfig(const SttClientConfig &config);
    SttClientConfig getConfig() const;

    bool start();
    void stop();
    bool isRunning() const;

private:
    void workerLoop();
    float computeRmsDb(const int16_t *samples, size_t count);

    AudioRingBuffer &ring_buffer_;
    CensorEngine &censor_;
    CaptionOutputSink &sink_;

    mutable std::mutex mutex_;
    SttClientConfig config_;
    std::atomic<bool> is_running_{false};
    std::thread worker_thread_;
    std::condition_variable cv_;
};

} // namespace ObsCaptions
