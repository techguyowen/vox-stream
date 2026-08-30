#include "stt_client.h"
#include <obs.h>
#include <vector>
#include <cmath>
#include <chrono>

namespace ObsCaptions {

SttWorkerClient::SttWorkerClient(AudioRingBuffer &ring_buffer, CensorEngine &censor, CaptionOutputSink &sink)
    : ring_buffer_(ring_buffer)
    , censor_(censor)
    , sink_(sink)
{
}

SttWorkerClient::~SttWorkerClient()
{
    stop();
}

void SttWorkerClient::setConfig(const SttClientConfig &config)
{
    std::lock_guard<std::mutex> lock(mutex_);
    config_ = config;
}

SttClientConfig SttWorkerClient::getConfig() const
{
    std::lock_guard<std::mutex> lock(mutex_);
    return config_;
}

bool SttWorkerClient::start()
{
    if (is_running_) {
        return true;
    }

    is_running_ = true;
    worker_thread_ = std::thread(&SttWorkerClient::workerLoop, this);
    blog(LOG_INFO, "[Live Captions] STT Worker Thread started.");
    return true;
}

void SttWorkerClient::stop()
{
    if (!is_running_) {
        return;
    }

    is_running_ = false;
    cv_.notify_all();

    if (worker_thread_.joinable()) {
        worker_thread_.join();
    }
    blog(LOG_INFO, "[Live Captions] STT Worker Thread stopped.");
}

bool SttWorkerClient::isRunning() const
{
    return is_running_;
}

float SttWorkerClient::computeRmsDb(const int16_t *samples, size_t count)
{
    if (!samples || count == 0) {
        return -100.0f;
    }
    double sum_sq = 0.0;
    for (size_t i = 0; i < count; ++i) {
        double s = static_cast<double>(samples[i]) / 32768.0;
        sum_sq += s * s;
    }
    double rms = std::sqrt(sum_sq / static_cast<double>(count));
    if (rms <= 1e-5) {
        return -100.0f;
    }
    return static_cast<float>(20.0 * std::log10(rms));
}

void SttWorkerClient::workerLoop()
{
    std::vector<int16_t> chunk_buffer(1600); // 100ms chunk at 16kHz
    auto last_clear_check = std::chrono::steady_clock::now();

    while (is_running_) {
        size_t available = ring_buffer_.availableSamples();

        if (available >= 1600) {
            size_t read_count = ring_buffer_.pop(chunk_buffer.data(), 1600);
            if (read_count > 0) {
                float db = computeRmsDb(chunk_buffer.data(), read_count);

                // Voice Activity Check
                if (db >= config_.noise_gate_db) {
                    // Audio active -> Process chunk with streaming STT pipeline
                    // For example, when running with the embedded/local engine:
                    // (Interim & final words are passed through censor and dispatched to sink)
                }
            }
        }

        // Periodic auto-clear check
        auto now = std::chrono::steady_clock::now();
        if (std::chrono::duration_cast<std::chrono::milliseconds>(now - last_clear_check).count() >= 200) {
            sink_.checkAutoClear();
            last_clear_check = now;
        }

        std::this_thread::sleep_for(std::chrono::milliseconds(20));
    }
}

} // namespace ObsCaptions
