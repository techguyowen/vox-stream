#pragma once

#include <QDialog>
#include <QComboBox>
#include <QLineEdit>
#include <QCheckBox>
#include <QSlider>
#include <QLabel>
#include <QPushButton>
#include <QTableWidget>
#include <QTabWidget>

namespace ObsCaptions {

class CaptionsSettingsDialog : public QDialog {
    Q_OBJECT

public:
    explicit CaptionsSettingsDialog(QWidget *parent = nullptr);
    ~CaptionsSettingsDialog() override;

private slots:
    void onSaveClicked();
    void onResetDefaultsClicked();
    void onAddReplacementClicked();
    void onDeleteReplacementClicked();
    void onEngineChanged(int index);

private:
    void setupUI();
    void loadSettings();
    void saveSettings();

    QTabWidget *tabs_;

    // General & Engine Tab
    QComboBox *comboEngine_;
    QComboBox *comboLanguage_;
    QLineEdit *editApiKey_;
    QLineEdit *editCredsPath_;
    QComboBox *comboTextSource_;
    QCheckBox *checkCea608_;

    // Filter Tab
    QCheckBox *checkCensorEnabled_;
    QComboBox *comboCensorMode_;
    QCheckBox *checkProfanity_;
    QCheckBox *checkBlasphemy_;
    QCheckBox *checkCrude_;
    QTableWidget *tableReplacements_;
    QLineEdit *editRepOriginal_;
    QLineEdit *editRepWholesome_;
    QLineEdit *editBlacklist_;
    QLineEdit *editWhitelist_;

    // Audio & Noise Gate Tab
    QSlider *sliderNoiseGate_;
    QLabel *labelNoiseGateVal_;
    QSlider *sliderAutoClear_;
    QLabel *labelAutoClearVal_;

    // Translation Tab
    QCheckBox *checkTranslationEnabled_;
    QComboBox *comboTranslationTarget_;
    QComboBox *comboTranslationMode_;
};

} // namespace ObsCaptions
