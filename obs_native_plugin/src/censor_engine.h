#pragma once

#include <string>
#include <vector>
#include <unordered_map>
#include <unordered_set>
#include <regex>
#include <mutex>

namespace ObsCaptions {

enum class CensorMode {
    Asterisk,
    Replacement,
    CensoredTag,
    DropSentence
};

struct CensorConfig {
    bool enabled = true;
    CensorMode mode = CensorMode::Replacement;
    bool filter_standard_profanity = true;
    bool filter_church_blasphemy = true;
    bool filter_crude_terms = true;
    std::vector<std::string> custom_blacklist;
    std::vector<std::string> custom_whitelist;
    std::unordered_map<std::string, std::string> custom_replacements;
};

class CensorEngine {
public:
    CensorEngine();
    explicit CensorEngine(const CensorConfig &config);

    void setConfig(const CensorConfig &config);
    CensorConfig getConfig() const;

    // Returns pair: (filtered_text, was_censored)
    std::pair<std::string, bool> filterText(const std::string &input);

    void addBlacklistWord(const std::string &word);
    void removeBlacklistWord(const std::string &word);
    void addWhitelistWord(const std::string &word);
    void removeWhitelistWord(const std::string &word);
    void setReplacement(const std::string &original, const std::string &replacement);
    void removeReplacement(const std::string &original);

private:
    void rebuildDictionary();
    std::string maskWord(const std::string &word) const;

    mutable std::mutex mutex_;
    CensorConfig config_;
    std::vector<std::pair<std::regex, std::string>> blacklist_patterns_;
    std::unordered_set<std::string> whitelist_set_;
    std::unordered_map<std::string, std::string> replacements_;
};

} // namespace ObsCaptions
