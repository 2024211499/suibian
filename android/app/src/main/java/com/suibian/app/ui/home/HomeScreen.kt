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
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
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
import java.text.NumberFormat
import java.time.LocalTime
import kotlin.math.roundToInt

@Composable
fun HomeScreen(viewModel: HomeViewModel = hiltViewModel()) {
    val context = LocalContext.current
    val message by viewModel.message.collectAsState()
    val state by viewModel.state.collectAsState()
    val savedPlans by viewModel.savedPlans.collectAsState()
    var input by rememberSaveable { mutableStateOf("") }

    // 出门提醒要 POST_NOTIFICATIONS 运行时授权（Android 13+）；拒绝只影响通知显示，闹钟本身照设。
    val notifPermissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted -> viewModel.debugScheduleDepartAlarm(notificationsGranted = granted) }

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
            Card(modifier = Modifier.fillMaxWidth()) {
                Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    Text(plan.summary.title, style = MaterialTheme.typography.titleLarge)
                    Text("${plan.summary.date} · 版本 ${plan.summary.version}")
                    plan.summary.nodes.forEach { stop ->
                        val latest = stop.window?.latest
                        Text(if (latest == null) stop.name else "${stop.name} · 最晚 $latest")
                    }
                    state.inspection?.let { result ->
                        Text("计划达成把握 ${formatProbability(result.probability)}（按 07:00 出发估算）")
                        Text(result.departBy?.let { "$it 前出门" } ?: "当前无满足目标把握的出发时刻")
                        plan.summary.nodes.firstOrNull { it.id == result.weakest?.node }?.let { stop ->
                            Text("最薄弱环节是${stop.name}")
                        }
                    }
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        Button(onClick = viewModel::savePlan, enabled = !state.planBusy && !state.saved) {
                            Text(if (state.saved) "已保存" else "保存")
                        }
                        Button(onClick = viewModel::inspectPlan, enabled = !state.planBusy) { Text("体检") }
                    }
                    TextButton(onClick = viewModel::simulateRain, enabled = !state.planBusy) { Text("模拟降雨") }
                    state.rain?.let { RainResults(it, plan.summary.nodes) }
                }
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

@Composable
private fun RainResults(comparison: RainComparison, stops: List<PlanStop>) {
    val before = formatProbability(comparison.before.probability)
    val after = formatProbability(comparison.after.probability)
    Text("降雨模拟 · 首段延误 15 分钟", style = MaterialTheme.typography.titleMedium)
    Text("计划达成把握 $before → $after")
    if (comparison.affected.isEmpty()) Text("后续活动的计算结果未变化")
    comparison.affected.forEach { impact ->
        val name = stops.firstOrNull { it.id == impact.node }?.name ?: impact.node
        val previous = formatArrival(impact.before.arrivalP90)
        val updated = formatArrival(impact.after.arrivalP90)
        Text("$name · 较保守到达估算 $previous → $updated")
    }
    if (comparison.options.isEmpty()) Text("当前没有达到目标把握的方案")
    comparison.options.forEach { option ->
        val label = if (option.id == "keep_route") "保留原路线" else "调整方案"
        Text("$label · 达成把握 ${formatProbability(option.sim.probability)}")
    }
}

private fun formatProbability(value: Double) = NumberFormat.getPercentInstance().apply {
    maximumFractionDigits = 1
}.format(value)

private fun formatArrival(minute: Double): String {
    val minutes = minute.roundToInt()
    val day = Math.floorDiv(minutes, 1440)
    val time = LocalTime.ofSecondOfDay(Math.floorMod(minutes, 1440) * 60L)
    return if (day == 0) time.toString() else "第 ${day + 1} 天 $time"
}
