// 仓库级插件声明。版本号统一在 gradle/libs.versions.toml 管理；
// W1 用 Android Studio 打开时按它建议的稳定版核对一遍（两人方案 W1 清单）。
plugins {
    alias(libs.plugins.android.application) apply false
    alias(libs.plugins.kotlin.android) apply false
    alias(libs.plugins.kotlin.compose) apply false
    alias(libs.plugins.kotlin.serialization) apply false
    alias(libs.plugins.hilt) apply false
    alias(libs.plugins.ksp) apply false
}
