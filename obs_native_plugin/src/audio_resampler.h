#pragma once

#include <vector>
#include <cstdint>
#include <mutex>
#include <condition_variable>

namespace ObsCaptions {

class AudioRingBuffer {
public:
    explicit AudioRingBuffer(size_t capacity_bytes = 64000); // ~2 seconds buffer at 16kHz 16-bit mono

    void push(const int16_t *data, size_t samples);
    size_t pop(int16_t *dest, size_t max_samples);
    void clear();
    size_t availableSamples() const;

private:
    mutable std::mutex mutex_;
    std::vector<int16_t> buffer_;
    size_t head_ = 0;
    size_t tail_ = 0;
    size_t count_ = 0;
    size_t capacity_ = 0;
};

class AudioResampler {
public:
    AudioResampler(uint32_t input_sample_rate = 48000, uint32_t input_channels = 2);

    void setInputFormat(uint32_t sample_rate, uint32_t channels);

    // Process incoming OBS planar float32 audio and push 16kHz 16-bit PCM into ring buffer
    void processObsAudio(const float *const *data, size_t frames, AudioRingBuffer &ring_buffer);

private:
    uint32_t input_sample_rate_ = 48000;
    uint32_t input_channels_ = 2;
    static constexpr uint32_t TARGET_SAMPLE_RATE = 16000;

    double resample_ratio_ = 16000.0 / 48000.0;
    double sample_pos_ = 0.0;
    std::vector<float> mono_scratch_;
};

} // namespace ObsCaptions
