#include "audio_resampler.h"
#include <algorithm>
#include <cmath>

namespace ObsCaptions {

AudioRingBuffer::AudioRingBuffer(size_t capacity_samples)
    : capacity_(capacity_samples)
{
    buffer_.resize(capacity_);
}

void AudioRingBuffer::push(const int16_t *data, size_t samples)
{
    std::lock_guard<std::mutex> lock(mutex_);
    for (size_t i = 0; i < samples; ++i) {
        buffer_[head_] = data[i];
        head_ = (head_ + 1) % capacity_;
        if (count_ < capacity_) {
            count_++;
        } else {
            // Overwriting oldest
            tail_ = (tail_ + 1) % capacity_;
        }
    }
}

size_t AudioRingBuffer::pop(int16_t *dest, size_t max_samples)
{
    std::lock_guard<std::mutex> lock(mutex_);
    size_t to_read = std::min(max_samples, count_);
    for (size_t i = 0; i < to_read; ++i) {
        dest[i] = buffer_[tail_];
        tail_ = (tail_ + 1) % capacity_;
    }
    count_ -= to_read;
    return to_read;
}

void AudioRingBuffer::clear()
{
    std::lock_guard<std::mutex> lock(mutex_);
    head_ = 0;
    tail_ = 0;
    count_ = 0;
}

size_t AudioRingBuffer::availableSamples() const
{
    std::lock_guard<std::mutex> lock(mutex_);
    return count_;
}

AudioResampler::AudioResampler(uint32_t input_sample_rate, uint32_t input_channels)
{
    setInputFormat(input_sample_rate, input_channels);
}

void AudioResampler::setInputFormat(uint32_t sample_rate, uint32_t channels)
{
    input_sample_rate_ = (sample_rate > 0) ? sample_rate : 48000;
    input_channels_ = (channels > 0) ? channels : 2;
    resample_ratio_ = static_cast<double>(TARGET_SAMPLE_RATE) / static_cast<double>(input_sample_rate_);
    sample_pos_ = 0.0;
}

void AudioResampler::processObsAudio(const float *const *data, size_t frames, AudioRingBuffer &ring_buffer)
{
    if (!data || frames == 0) {
        return;
    }

    // 1. Downmix input channels to mono float32
    mono_scratch_.resize(frames);
    if (input_channels_ == 1 || !data[1]) {
        std::copy(data[0], data[0] + frames, mono_scratch_.begin());
    } else {
        const float inv_channels = 1.0f / static_cast<float>(input_channels_);
        for (size_t f = 0; f < frames; ++f) {
            float sum = 0.0f;
            for (uint32_t c = 0; c < input_channels_; ++c) {
                if (data[c]) {
                    sum += data[c][f];
                }
            }
            mono_scratch_[f] = sum * inv_channels;
        }
    }

    // 2. Resample from input_sample_rate to 16,000 Hz using linear interpolation
    std::vector<int16_t> pcm16_out;
    const double step = static_cast<double>(input_sample_rate_) / static_cast<double>(TARGET_SAMPLE_RATE);

    while (sample_pos_ < static_cast<double>(frames)) {
        size_t idx0 = static_cast<size_t>(sample_pos_);
        size_t idx1 = std::min(idx0 + 1, frames - 1);
        double frac = sample_pos_ - static_cast<double>(idx0);

        float s0 = mono_scratch_[idx0];
        float s1 = mono_scratch_[idx1];
        float sample = s0 + static_cast<float>(frac * (s1 - s0));

        // Clip and convert to 16-bit linear PCM
        sample = std::max(-1.0f, std::min(1.0f, sample));
        int16_t pcm_sample = static_cast<int16_t>(sample * 32767.0f);
        pcm16_out.push_back(pcm_sample);

        sample_pos_ += step;
    }

    sample_pos_ -= static_cast<double>(frames);

    // 3. Push converted 16kHz PCM samples into thread-safe ring buffer
    if (!pcm16_out.empty()) {
        ring_buffer.push(pcm16_out.data(), pcm16_out.size());
    }
}

} // namespace ObsCaptions
