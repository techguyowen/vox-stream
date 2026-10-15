#include "stt_client.h"
#include <obs.h>
#include <vector>
#include <cmath>
#include <chrono>
#include <memory>

#include <httplib.h>

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

    std::unique_ptr<httplib::Client> http_client;
    std::string connected_url;

    auto get_or_create_client = [&](const std::string &url) -> httplib::Client* {
        if (!http_client || connected_url != url) {
            http_client = std::make_unique<httplib::Client>(url.c_str());
            http_client->set_keep_alive(true);
            http_client->set_connection_timeout(0, 500000); // 500ms
            http_client->set_read_timeout(1, 0);           // 1s
            connected_url = url;
        }
        return http_client.get();
    };

    while (is_running_) {
        size_t available = ring_buffer_.availableSamples();

        if (available >= 1600) {
            size_t read_count = ring_buffer_.pop(chunk_buffer.data(), 1600);
            if (read_count > 0) {
                float db = computeRmsDb(chunk_buffer.data(), read_count);

                SttClientConfig cfg = getConfig();
                // Voice Activity Check
                if (db >= cfg.noise_gate_db) {
                    httplib::Client *cli = get_or_create_client(cfg.backend_url);

                    httplib::Headers headers = {
                        {"Authorization", "Bearer " + cfg.api_key}
                    };

                    auto res = cli->Post(
                        "/api/audio/chunk",
                        headers,
                        reinterpret_cast<const char*>(chunk_buffer.data()),
                        read_count * sizeof(int16_t),
                        "application/octet-stream"
                    );
                    if (!res || res->status != 200) {
                        blog(LOG_WARNING, "[Live Captions] Failed to send audio chunk to backend, reconnecting.");
                        http_client.reset();
                        connected_url.clear();
                    }
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
