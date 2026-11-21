package com.suibian.app.ui.theme

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable

@Composable
fun SuibianTheme(content: @Composable () -> Unit) {
    // TODO(W2): 配色与字号定稿（方案卡、依据面板要用来源标签色）
    MaterialTheme(
        colorScheme = lightColorScheme(),
        content = content,
    )
}
