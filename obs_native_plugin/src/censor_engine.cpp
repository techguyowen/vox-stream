#include "censor_engine.h"
#include <algorithm>
#include <cctype>

namespace ObsCaptions {

static const std::vector<std::string> DEFAULT_STANDARD_PROFANITIES = {
    "fuck", "fucking", "fucked", "fucker", "fuckers", "motherfucker", "motherfucking",
    "shit", "shitty", "shitting", "bullshit", "horseshit",
    "bitch", "bitches", "bitching", "bitchy",
    "ass", "asshole", "assholes", "dumbass", "jackass", "badass",
    "bastard", "bastards",
    "cunt", "cunts",
    "dick", "dicks", "dickhead",
    "cock", "cocks", "cocksucker",
    "pussy", "pussies",
    "slut", "sluts", "whore", "whores",
    "prick", "pricks", "twat", "twats",
    "wanker", "wankers",
};

static const std::vector<std::string> DEFAULT_CHURCH_BLASPHEMIES = {
    "goddamn", "goddammit", "god damn", "god dammit", "god damned",
    "holy shit", "holy fuck", "holy hell",
    "damn", "dammit", "damned", "damning",
    "hell", "hellish",
};

static const std::vector<std::string> DEFAULT_CRUDE_TERMS = {
    "tits", "boobs", "boner", "dildo", "blowjob", "handjob", "cum", "cumming",
    "orgasm", "masturbate", "masturbation", "horny", "retard", "retarded",
    "nigger", "nigga", "faggot", "fag", "dyke", "kike", "chink", "spic",
};

static const std::unordered_map<std::string, std::string> DEFAULT_WHOLESOME_REPLACEMENTS = {
    {"fuck", "fudge"},
    {"fucking", "flipping"},
    {"fucked", "messed up"},
    {"fucker", "rascal"},
    {"motherfucker", "monster"},
    {"shit", "shoot"},
    {"shitty", "lousy"},
    {"bullshit", "nonsense"},
    {"bitch", "complainer"},
    {"bitches", "people"},
    {"bitching", "grumbling"},
    {"ass", "bottom"},
    {"asshole", "jerk"},
    {"dumbass", "silly person"},
    {"jackass", "fool"},
    {"bastard", "rogue"},
    {"cunt", "scoundrel"},
    {"dick", "pest"},
    {"dickhead", "fool"},
    {"cock", "rooster"},
    {"pussy", "wimp"},
    {"whore", "traitor"},
    {"slut", "wild one"},
    {"damn", "darn"},
    {"dammit", "drat"},
    {"damned", "blasted"},
    {"goddamn", "gosh darn"},
    {"goddammit", "gosh darn it"},
    {"god damn", "gosh darn"},
    {"holy shit", "holy cow"},
    {"holy fuck", "my word"},
    {"holy hell", "my goodness"},
    {"hell", "heck"},
    {"hellish", "rough"},
};

static const std::vector<std::string> DEFAULT_WHITELIST = {
    "jesus christ", "jesus", "christ", "god", "lord", "holy spirit", "holy ghost",
    "amen", "hallelujah", "bible", "scripture", "pastor", "preacher", "worship",
    "pass", "passed", "passing", "passport", "compass", "surpass", "trespass",
    "grass", "glass", "class", "classic", "mass", "massive", "bass", "brass",
    "assume", "asset", "assist", "assistant", "assess", "assessment", "associate",
    "dickens", "hitchcock", "peacock", "cockpit", "cockatoo", "shuttlecock",
    "scrap", "scrappy", "therapist", "title", "butter", "button", "document",
    "push", "pushed", "bullet", "analysis", "canal", "county", "country",
};

static std::string toLower(const std::string &str)
{
    std::string lower = str;
    std::transform(lower.begin(), lower.end(), lower.begin(), [](unsigned char c) {
        return static_cast<char>(std::tolower(c));
    });
    return lower;
}

CensorEngine::CensorEngine()
{
    rebuildDictionary();
}

CensorEngine::CensorEngine(const CensorConfig &config)
    : config_(config)
{
    rebuildDictionary();
}

void CensorEngine::setConfig(const CensorConfig &config)
{
    std::lock_guard<std::mutex> lock(mutex_);
    config_ = config;
    rebuildDictionary();
}

CensorConfig CensorEngine::getConfig() const
{
    std::lock_guard<std::mutex> lock(mutex_);
    return config_;
}

void CensorEngine::rebuildDictionary()
{
    whitelist_set_.clear();
    for (const auto &w : DEFAULT_WHITELIST) {
        whitelist_set_.insert(toLower(w));
    }
    for (const auto &w : config_.custom_whitelist) {
        if (!w.empty()) {
            whitelist_set_.insert(toLower(w));
        }
    }

    replacements_ = DEFAULT_WHOLESOME_REPLACEMENTS;
    for (const auto &[k, v] : config_.custom_replacements) {
        if (!k.empty() && !v.empty()) {
            replacements_[toLower(k)] = v;
        }
    }

    std::vector<std::string> terms;
    if (config_.filter_standard_profanity) {
        terms.insert(terms.end(), DEFAULT_STANDARD_PROFANITIES.begin(), DEFAULT_STANDARD_PROFANITIES.end());
    }
    if (config_.filter_church_blasphemy) {
        terms.insert(terms.end(), DEFAULT_CHURCH_BLASPHEMIES.begin(), DEFAULT_CHURCH_BLASPHEMIES.end());
    }
    if (config_.filter_crude_terms) {
        terms.insert(terms.end(), DEFAULT_CRUDE_TERMS.begin(), DEFAULT_CRUDE_TERMS.end());
    }
    for (const auto &w : config_.custom_blacklist) {
        if (!w.empty()) {
            terms.push_back(toLower(w));
        }
    }
    for (const auto &[k, v] : replacements_) {
        terms.push_back(k);
    }

    // Sort terms by length descending
    std::sort(terms.begin(), terms.end(), [](const std::string &a, const std::string &b) {
        return a.length() > b.length();
    });
    // Remove duplicates
    terms.erase(std::unique(terms.begin(), terms.end()), terms.end());

    blacklist_patterns_.clear();
    for (const auto &t : terms) {
        try {
            // Regex word boundary pattern
            std::string pat = "\\b" + t + "\\b";
            blacklist_patterns_.emplace_back(std::regex(pat, std::regex_constants::icase), t);
        } catch (...) {
            // Ignore invalid regex
        }
    }
}

std::string CensorEngine::maskWord(const std::string &word) const
{
    if (word.length() <= 2) {
        return std::string(word.length(), '*');
    }
    return word.substr(0, 1) + std::string(word.length() - 1, '*');
}

std::pair<std::string, bool> CensorEngine::filterText(const std::string &input)
{
    std::lock_guard<std::mutex> lock(mutex_);
    if (!config_.enabled || input.empty()) {
        return {input, false};
    }

    std::string result = input;
    bool was_censored = false;

    for (const auto &[pattern, raw_term] : blacklist_patterns_) {
        std::smatch match;
        std::string search_str = result;
        std::string new_result = "";
        size_t last_pos = 0;

        auto words_begin = std::sregex_iterator(result.begin(), result.end(), pattern);
        auto words_end = std::sregex_iterator();

        if (words_begin == words_end) {
            continue;
        }

        for (std::sregex_iterator i = words_begin; i != words_end; ++i) {
            std::smatch m = *i;
            std::string matched_word = m.str();
            std::string lower_word = toLower(matched_word);

            // Skip if in whitelist
            if (whitelist_set_.find(lower_word) != whitelist_set_.end()) {
                continue;
            }

            was_censored = true;

            if (config_.mode == CensorMode::DropSentence) {
                return {"", true};
            }

            std::string replacement;
            if (config_.mode == CensorMode::CensoredTag) {
                replacement = "[CENSORED]";
            } else if (config_.mode == CensorMode::Replacement) {
                auto it = replacements_.find(lower_word);
                if (it != replacements_.end()) {
                    replacement = it->second;
                    // Preserve capitalization
                    if (!matched_word.empty() && std::isupper(static_cast<unsigned char>(matched_word[0]))) {
                        replacement[0] = static_cast<char>(std::toupper(static_cast<unsigned char>(replacement[0])));
                    }
                } else {
                    replacement = maskWord(matched_word);
                }
            } else {
                // Default: Asterisk
                replacement = maskWord(matched_word);
            }

            new_result.append(result, last_pos, m.position() - last_pos);
            new_result.append(replacement);
            last_pos = m.position() + m.length();
        }

        new_result.append(result, last_pos, result.length() - last_pos);
        result = new_result;
    }

    return {result, was_censored};
}

void CensorEngine::addBlacklistWord(const std::string &word)
{
    std::lock_guard<std::mutex> lock(mutex_);
    config_.custom_blacklist.push_back(word);
    rebuildDictionary();
}

void CensorEngine::removeBlacklistWord(const std::string &word)
{
    std::lock_guard<std::mutex> lock(mutex_);
    std::string lower = toLower(word);
    config_.custom_blacklist.erase(
        std::remove_if(config_.custom_blacklist.begin(), config_.custom_blacklist.end(),
            [&lower](const std::string &w) { return toLower(w) == lower; }),
        config_.custom_blacklist.end());
    rebuildDictionary();
}

void CensorEngine::addWhitelistWord(const std::string &word)
{
    std::lock_guard<std::mutex> lock(mutex_);
    config_.custom_whitelist.push_back(word);
    rebuildDictionary();
}

void CensorEngine::removeWhitelistWord(const std::string &word)
{
    std::lock_guard<std::mutex> lock(mutex_);
    std::string lower = toLower(word);
    config_.custom_whitelist.erase(
        std::remove_if(config_.custom_whitelist.begin(), config_.custom_whitelist.end(),
            [&lower](const std::string &w) { return toLower(w) == lower; }),
        config_.custom_whitelist.end());
    rebuildDictionary();
}

void CensorEngine::setReplacement(const std::string &original, const std::string &replacement)
{
    std::lock_guard<std::mutex> lock(mutex_);
    config_.custom_replacements[toLower(original)] = replacement;
    rebuildDictionary();
}

void CensorEngine::removeReplacement(const std::string &original)
{
    std::lock_guard<std::mutex> lock(mutex_);
    config_.custom_replacements.erase(toLower(original));
    rebuildDictionary();
}

} // namespace ObsCaptions
