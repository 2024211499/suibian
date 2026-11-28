package com.suibian.app.ui.home

import android.Manifest
import android.content.pm.PackageManager
import android.os.Build
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.key
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import androidx.hilt.navigation.compose.hiltViewModel
import com.suibian.app.BuildConfig
import com.suibian.app.deployment.DeploymentScreen
import com.suibian.app.deployment.DeploymentViewModel

@Composable
fun HomeScreen(viewModel: HomeViewModel = hiltViewModel(), deploymentViewModel: DeploymentViewModel = hiltViewModel()) {
    val context = LocalContext.current
    val message by viewModel.message.collectAsState()
    val state by viewModel.state.collectAsState()
    val savedPlans by viewModel.savedPlans.collectAsState()
    val deployment by deploymentViewModel.editor.collectAsState()
    var input by rememberSaveable { mutableStateOf("") }

    // 出门提醒要 POST_NOTIFICATIONS 运行时授权（Android 13+）；拒绝只影响通知显示，闹钟本身照设。
    val notifPermissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted -> viewModel.debugScheduleDepartAlarm(notificationsGranted = granted) }

    LaunchedEffect(deployment != null) {
        if (deployment == null) viewModel.refreshSavedPreview()
    }

    deployment?.let {
        DeploymentScreen(it, deploymentViewModel)
        return
    }

    Column(
        modifier = Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Text("随变", style = MaterialTheme.typography.headlineMedium)
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(if (state.healthLoading) "正在检查服务…" else state.health.orEmpty())
            TextButton(onClick = viewModel::refreshHealth, enabled = !state.healthLoading) { Text("刷新") }
        }

        OutlinedTextField(
            value = input,
            onValueChange = { input = it },
            modifier = Modifier.fillMaxWidth(),
            label = { Text("说一句话") },
            placeholder = { Text("例如：明早8点三教上课") },
            minLines = 2,
        )
        Button(onClick = { viewModel.parse(input) }, enabled = input.isNotBlank() && !state.planBusy) {
            Text("录入计划")
        }
        state.notice?.let { Text(it) }
        state.plan?.let { plan ->
            key(plan.summary.id, plan.summary.version) {
                HomePlanCard(plan, state, viewModel) { deploymentViewModel.open(plan, state.departureMinute) }
            }
        }

        if (state.savedPlansUnavailable) Text("暂时无法读取已保存计划")
        if (savedPlans.isNotEmpty()) {
            Text("已保存计划", style = MaterialTheme.typography.titleMedium)
            for (entry in savedPlans) {
                TextButton(onClick = { viewModel.openSavedPlan(entry.entity) }, enabled = !state.planBusy) {
                    Text("${entry.title} · 版本 ${entry.entity.version}")
                }
            }
        }

        if (!BuildConfig.DEBUG) return@Column
        Text("闹钟调试", style = MaterialTheme.typography.titleSmall)
        Button(onClick = {
            val needsNotifPermission = Build.VERSION.SDK_INT >= 33 && ContextCompat.checkSelfPermission(
                context,
                Manifest.permission.POST_NOTIFICATIONS,
            ) != PackageManager.PERMISSION_GRANTED
            if (needsNotifPermission) {
                notifPermissionLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
            } else {
                viewModel.debugScheduleDepartAlarm()
            }
        }) { Text("30 分钟后出门闹钟") }
        Button(onClick = { viewModel.debugScheduleTick() }) { Text("立即 tick") }
        message?.let { Text(it, style = MaterialTheme.typography.bodySmall) }

        // TODO(W4): 生成进度页入口（SSE）
        // TODO(W5): 截图导入、复盘卡、画像页、授权中心入口
    }
}
