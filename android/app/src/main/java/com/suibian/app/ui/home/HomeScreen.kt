package com.suibian.app.ui.home

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp

@Composable
fun HomeScreen() {
    Column(
        modifier = Modifier.fillMaxSize().padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Text("随变", style = MaterialTheme.typography.headlineMedium)
        Text("一句话出计划，变了也不慌", style = MaterialTheme.typography.bodyMedium)
        Text("W1 骨架：先对着服务端 mock 调通 /v1/health", style = MaterialTheme.typography.bodySmall)
        // TODO(W2): 大输入框"说一句话"+ 今天/明天的计划卡列表（技术文档 10.2）
        // TODO(W4): 生成进度页入口（SSE）
        // TODO(W5): 截图导入、复盘卡、画像页、授权中心入口
    }
}
