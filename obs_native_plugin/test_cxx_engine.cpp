#include "src/censor_engine.h"
#include "src/audio_resampler.h"
#include <iostream>
#include <cassert>
#include <vector>

using namespace ObsCaptions;

void testCensorEngine()
{
    std::cout << "[Test] Running C++ CensorEngine tests..." << std::endl;
    CensorConfig cfg;
    cfg.enabled = true;
    cfg.mode = CensorMode::Replacement;
    CensorEngine engine(cfg);

    // 1. Swear word replacement
    auto [filtered1, was_censored1] = engine.filterText("What the fuck is going on?");
    std::cout << "  Input: 'What the fuck is going on?' -> Output: '" << filtered1 << "'" << std::endl;
    assert(was_censored1);
    assert(filtered1 == "What the fudge is going on?");

    // 2. Blasphemous oaths
    auto [filtered2, was_censored2] = engine.filterText("Oh goddammit, that hurt!");
    assert(was_censored2);
    assert(filtered2 == "Oh gosh darn it, that hurt!");

    // 3. Sacred names whitelist preservation
    auto [filtered3, was_censored3] = engine.filterText("We worship Jesus Christ our Lord and Savior.");
    std::cout << "  Input: 'We worship Jesus Christ our Lord and Savior.' -> Output: '" << filtered3 << "'" << std::endl;
    assert(!was_censored3);
    assert(filtered3 == "We worship Jesus Christ our Lord and Savior.");

    // 4. Asterisk mode
    cfg.mode = CensorMode::Asterisk;
    engine.setConfig(cfg);
    auto [filtered4, was_censored4] = engine.filterText("This is shit");
    assert(was_censored4);
    assert(filtered4 == "This is s***");

    // 5. Custom CRUD
    engine.addBlacklistWord("sillygoose");
    auto [filtered5, was_censored5] = engine.filterText("You are a sillygoose");
    assert(was_censored5);
    assert(filtered5 == "You are a s*********");

    engine.removeBlacklistWord("sillygoose");
    auto [filtered6, was_censored6] = engine.filterText("You are a sillygoose");
    assert(!was_censored6);

    std::cout << "[PASS] All C++ CensorEngine tests passed successfully!" << std::endl;
}

void testAudioResampler()
{
    std::cout << "[Test] Running C++ AudioResampler tests..." << std::endl;
    AudioRingBuffer ring_buffer(16000);
    AudioResampler resampler(48000, 2);

    // Create 480 frames of stereo audio (10ms at 48kHz)
    std::vector<float> left(480, 0.5f);
    std::vector<float> right(480, 0.5f);
    const float *data[2] = {left.data(), right.data()};

    resampler.processObsAudio(data, 480, ring_buffer);

    // 480 frames at 48kHz should convert to ~160 samples at 16kHz
    size_t available = ring_buffer.availableSamples();
    std::cout << "  Processed 480 frames at 48kHz -> Resampled samples at 16kHz: " << available << std::endl;
    assert(available >= 150 && available <= 170);

    std::vector<int16_t> out_pcm(200);
    size_t read_count = ring_buffer.pop(out_pcm.data(), 200);
    assert(read_count == available);
    assert(ring_buffer.availableSamples() == 0);

    std::cout << "[PASS] All C++ AudioResampler tests passed successfully!" << std::endl;
}

int main()
{
    testCensorEngine();
    testAudioResampler();
    std::cout << "\n🎉 ALL NATIVE C++ ENGINE TESTS PASSED!" << std::endl;
    return 0;
}
