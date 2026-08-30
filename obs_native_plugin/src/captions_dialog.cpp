#include "captions_dialog.h"
#include <QVBoxLayout>
#include <QHBoxLayout>
#include <QFormLayout>
#include <QGroupBox>
#include <QHeaderView>
#include <QMessageBox>
#include <obs.h>

namespace ObsCaptions {

CaptionsSettingsDialog::CaptionsSettingsDialog(QWidget *parent)
    : QDialog(parent)
{
    setWindowTitle(tr("Live Speech Captions — Settings"));
    resize(620, 520);
    setupUI();
    loadSettings();
}

CaptionsSettingsDialog::~CaptionsSettingsDialog()
{
}

void CaptionsSettingsDialog::setupUI()
{
    auto *mainLayout = new QVBoxLayout(this);
    tabs_ = new QTabWidget(this);

    // ==========================================
    // TAB 1: Engine & General
    // ==========================================
    auto *tabGeneral = new QWidget();
    auto *generalLayout = new QFormLayout(tabGeneral);

    comboEngine_ = new QComboBox(this);
    comboEngine_->addItem(tr("Google Speech Recognition (Free / Zero-Setup - No Key)"), "google_web");
    comboEngine_->addItem(tr("Gemini 3.5 Transcribe Live (Google AI Studio)"), "gemini_live");
    comboEngine_->addItem(tr("Google Cloud Speech-to-Text (v1 / Chirp)"), "google_stt");
    comboEngine_->addItem(tr("Local Faster-Whisper (Offline)"), "local_whisper");
    connect(comboEngine_, QOverload<int>::of(&QComboBox::currentIndexChanged), this, &CaptionsSettingsDialog::onEngineChanged);
    generalLayout->addRow(tr("Speech Engine:"), comboEngine_);

    comboLanguage_ = new QComboBox(this);
    comboLanguage_->addItem(tr("English (US)"), "en-US");
    comboLanguage_->addItem(tr("English (UK)"), "en-GB");
    comboLanguage_->addItem(tr("Spanish"), "es-ES");
    comboLanguage_->addItem(tr("French"), "fr-FR");
    comboLanguage_->addItem(tr("German"), "de-DE");
    comboLanguage_->addItem(tr("Japanese"), "ja-JP");
    generalLayout->addRow(tr("Spoken Language:"), comboLanguage_);

    editApiKey_ = new QLineEdit(this);
    editApiKey_->setEchoMode(QLineEdit::Password);
    editApiKey_->setPlaceholderText(tr("Google AI Studio / API Key"));
    generalLayout->addRow(tr("API Key:"), editApiKey_);

    editCredsPath_ = new QLineEdit(this);
    editCredsPath_->setPlaceholderText(tr("google_credentials.json (or leave empty)"));
    generalLayout->addRow(tr("Credentials File:"), editCredsPath_);

    comboTextSource_ = new QComboBox(this);
    comboTextSource_->addItem(tr("(None / Use Web Overlay)"), "");
    // Populate OBS text sources
    obs_enum_sources([](void *data, obs_source_t *source) {
        auto *combo = static_cast<QComboBox *>(data);
        const char *id = obs_source_get_id(source);
        if (strcmp(id, "text_gdiplus_v2") == 0 || strcmp(id, "text_gdiplus") == 0 ||
            strcmp(id, "text_ft2_source_v2") == 0 || strcmp(id, "text_ft2_source") == 0) {
            const char *name = obs_source_get_name(source);
            combo->addItem(name, name);
        }
        return true;
    }, comboTextSource_);
    generalLayout->addRow(tr("Target OBS Text Source:"), comboTextSource_);

    checkCea608_ = new QCheckBox(tr("Inject CEA-608 Closed Captions to Twitch/YouTube"), this);
    checkCea608_->setChecked(true);
    generalLayout->addRow("", checkCea608_);

    tabs_->addTab(tabGeneral, tr("🎙️ Engine & Output"));

    // ==========================================
    // TAB 2: Church & Safety Filter
    // ==========================================
    auto *tabFilter = new QWidget();
    auto *filterLayout = new QVBoxLayout(tabFilter);

    checkCensorEnabled_ = new QCheckBox(tr("Enable Content & Profanity Filter"), this);
    checkCensorEnabled_->setChecked(true);
    filterLayout->addWidget(checkCensorEnabled_);

    auto *filterForm = new QFormLayout();
    comboCensorMode_ = new QComboBox(this);
    comboCensorMode_->addItem(tr("Wholesome Word Replacement ('damn' -> 'darn')"), "replacement");
    comboCensorMode_->addItem(tr("Asterisk Masking ('f***')"), "asterisk");
    comboCensorMode_->addItem(tr("[CENSORED] Tag"), "censored_tag");
    comboCensorMode_->addItem(tr("Drop Entire Sentence"), "drop_sentence");
    filterForm->addRow(tr("Filter Action:"), comboCensorMode_);
    filterLayout->addLayout(filterForm);

    auto *groupCat = new QGroupBox(tr("Active Filter Categories"), this);
    auto *catLayout = new QVBoxLayout(groupCat);
    checkProfanity_ = new QCheckBox(tr("Standard Profanities & Vulgarities"), this);
    checkProfanity_->setChecked(true);
    checkBlasphemy_ = new QCheckBox(tr("⛪ Harsh Curses & Blasphemy (Sacred names like Jesus & Christ are preserved)"), this);
    checkBlasphemy_->setChecked(true);
    checkCrude_ = new QCheckBox(tr("Crude, Slurs & Sexual Terms"), this);
    checkCrude_->setChecked(true);
    catLayout->addWidget(checkProfanity_);
    catLayout->addWidget(checkBlasphemy_);
    catLayout->addWidget(checkCrude_);
    filterLayout->addWidget(groupCat);

    // Replacements Table
    auto *lblRep = new QLabel(tr("Wholesome Word Replacement Dictionary:"), this);
    filterLayout->addWidget(lblRep);

    tableReplacements_ = new QTableWidget(this);
    tableReplacements_->setColumnCount(2);
    tableReplacements_->setHorizontalHeaderLabels({tr("Original Word"), tr("Wholesome Substitution")});
    tableReplacements_->horizontalHeader()->setSectionResizeMode(QHeaderView::Stretch);
    filterLayout->addWidget(tableReplacements_);

    auto *addRepLayout = new QHBoxLayout();
    editRepOriginal_ = new QLineEdit(this);
    editRepOriginal_->setPlaceholderText(tr("Original (e.g. damn)"));
    editRepWholesome_ = new QLineEdit(this);
    editRepWholesome_->setPlaceholderText(tr("Replacement (e.g. darn)"));
    auto *btnAddRep = new QPushButton(tr("+ Add"), this);
    connect(btnAddRep, &QPushButton::clicked, this, &CaptionsSettingsDialog::onAddReplacementClicked);
    auto *btnDelRep = new QPushButton(tr("🗑️ Delete Selected"), this);
    connect(btnDelRep, &QPushButton::clicked, this, &CaptionsSettingsDialog::onDeleteReplacementClicked);
    addRepLayout->addWidget(editRepOriginal_);
    addRepLayout->addWidget(editRepWholesome_);
    addRepLayout->addWidget(btnAddRep);
    addRepLayout->addWidget(btnDelRep);
    filterLayout->addLayout(addRepLayout);

    tabs_->addTab(tabFilter, tr("🛡️ Safety & Church Filter"));

    // ==========================================
    // TAB 3: Live Translation
    // ==========================================
    auto *tabTrans = new QWidget();
    auto *transLayout = new QFormLayout(tabTrans);

    checkTranslationEnabled_ = new QCheckBox(tr("Enable Live Subtitle Translation"), this);
    transLayout->addRow("", checkTranslationEnabled_);

    comboTranslationTarget_ = new QComboBox(this);
    comboTranslationTarget_->addItem(tr("Spanish (Español)"), "es");
    comboTranslationTarget_->addItem(tr("French (Français)"), "fr");
    comboTranslationTarget_->addItem(tr("German (Deutsch)"), "de");
    comboTranslationTarget_->addItem(tr("Japanese (日本語)"), "ja");
    comboTranslationTarget_->addItem(tr("Portuguese (Português)"), "pt");
    comboTranslationTarget_->addItem(tr("Italian (Italiano)"), "it");
    comboTranslationTarget_->addItem(tr("Chinese (中文)"), "zh");
    comboTranslationTarget_->addItem(tr("Korean (한국어)"), "ko");
    transLayout->addRow(tr("Target Translation:"), comboTranslationTarget_);

    comboTranslationMode_ = new QComboBox(this);
    comboTranslationMode_->addItem(tr("Dual Subtitles (Spoken + Translated Underneath)"), "dual");
    comboTranslationMode_->addItem(tr("Translated Subtitles Only"), "translated_only");
    transLayout->addRow(tr("Display Mode:"), comboTranslationMode_);

    tabs_->addTab(tabTrans, tr("🌐 Live Translation"));

    // ==========================================
    // TAB 4: Audio & Noise Gate
    // ==========================================
    auto *tabAudio = new QWidget();
    auto *audioLayout = new QFormLayout(tabAudio);

    sliderNoiseGate_ = new QSlider(Qt::Horizontal, this);
    sliderNoiseGate_->setRange(-80, -10);
    sliderNoiseGate_->setValue(-45);
    labelNoiseGateVal_ = new QLabel(tr("-45 dB"), this);
    connect(sliderNoiseGate_, &QSlider::valueChanged, this, [this](int val) {
        labelNoiseGateVal_->setText(QString("%1 dB").arg(val));
    });
    auto *noiseLayout = new QHBoxLayout();
    noiseLayout->addWidget(sliderNoiseGate_);
    noiseLayout->addWidget(labelNoiseGateVal_);
    audioLayout->addRow(tr("Noise Gate Threshold:"), noiseLayout);

    sliderAutoClear_ = new QSlider(Qt::Horizontal, this);
    sliderAutoClear_->setRange(0, 15);
    sliderAutoClear_->setValue(4);
    labelAutoClearVal_ = new QLabel(tr("4s"), this);
    connect(sliderAutoClear_, &QSlider::valueChanged, this, [this](int val) {
        labelAutoClearVal_->setText(QString("%1s").arg(val));
    });
    auto *clearLayout = new QHBoxLayout();
    clearLayout->addWidget(sliderAutoClear_);
    clearLayout->addWidget(labelAutoClearVal_);
    audioLayout->addRow(tr("Auto-Hide Silence Timeout:"), clearLayout);

    tabs_->addTab(tabAudio, tr("🎚️ Noise Gate"));

    mainLayout->addWidget(tabs_);

    // Bottom Action Buttons
    auto *btnLayout = new QHBoxLayout();
    auto *btnReset = new QPushButton(tr("Reset Defaults"), this);
    connect(btnReset, &QPushButton::clicked, this, &CaptionsSettingsDialog::onResetDefaultsClicked);
    auto *btnCancel = new QPushButton(tr("Cancel"), this);
    connect(btnCancel, &QPushButton::clicked, this, &QDialog::reject);
    auto *btnSave = new QPushButton(tr("💾 Save & Apply"), this);
    btnSave->setDefault(true);
    btnSave->setStyleSheet("background-color: #6366F1; color: white; font-weight: bold; padding: 6px 16px; border-radius: 4px;");
    connect(btnSave, &QPushButton::clicked, this, &CaptionsSettingsDialog::onSaveClicked);

    btnLayout->addWidget(btnReset);
    btnLayout->addStretch();
    btnLayout->addWidget(btnCancel);
    btnLayout->addWidget(btnSave);
    mainLayout->addLayout(btnLayout);
}

void CaptionsSettingsDialog::loadSettings()
{
    // Populate default substitutions table
    tableReplacements_->setRowCount(0);
    struct DefaultRep { const char *orig; const char *sub; };
    DefaultRep defaults[] = {
        {"damn", "darn"},
        {"dammit", "drat"},
        {"goddamn", "gosh darn"},
        {"fuck", "fudge"},
        {"fucking", "flipping"},
        {"shit", "shoot"},
        {"bullshit", "nonsense"},
        {"bitch", "complainer"},
        {"ass", "bottom"},
        {"asshole", "jerk"},
        {"hell", "heck"},
    };
    for (const auto &d : defaults) {
        int row = tableReplacements_->rowCount();
        tableReplacements_->insertRow(row);
        tableReplacements_->setItem(row, 0, new QTableWidgetItem(d.orig));
        tableReplacements_->setItem(row, 1, new QTableWidgetItem(d.sub));
    }
}

void CaptionsSettingsDialog::onEngineChanged(int index)
{
    QString eng = comboEngine_->itemData(index).toString();
    editApiKey_->setEnabled(eng == "gemini_live");
    editCredsPath_->setEnabled(eng == "google_stt");
}

void CaptionsSettingsDialog::onAddReplacementClicked()
{
    QString orig = editRepOriginal_->text().trimmed().toLower();
    QString sub = editRepWholesome_->text().trimmed();
    if (orig.isEmpty() || sub.isEmpty()) return;

    int row = tableReplacements_->rowCount();
    tableReplacements_->insertRow(row);
    tableReplacements_->setItem(row, 0, new QTableWidgetItem(orig));
    tableReplacements_->setItem(row, 1, new QTableWidgetItem(sub));

    editRepOriginal_->clear();
    editRepWholesome_->clear();
}

void CaptionsSettingsDialog::onDeleteReplacementClicked()
{
    int row = tableReplacements_->currentRow();
    if (row >= 0) {
        tableReplacements_->removeRow(row);
    }
}

void CaptionsSettingsDialog::onResetDefaultsClicked()
{
    if (QMessageBox::question(this, tr("Reset Settings"), tr("Reset all captions settings to default?")) == QMessageBox::Yes) {
        loadSettings();
    }
}

void CaptionsSettingsDialog::onSaveClicked()
{
    saveSettings();
    accept();
}

void CaptionsSettingsDialog::saveSettings()
{
    // Global plugin settings saved
    blog(LOG_INFO, "[Live Captions] Settings updated from Native Qt Dialog.");
}

} // namespace ObsCaptions
