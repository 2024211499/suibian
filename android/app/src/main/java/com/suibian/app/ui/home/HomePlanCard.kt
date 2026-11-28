package com.suibian.app.ui.home

import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.relocation.BringIntoViewRequester
import androidx.compose.foundation.relocation.bringIntoViewRequester
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.FilterChip
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import java.text.NumberFormat
import java.time.LocalTime
import kotlin.math.roundToInt

@Composable
@OptIn(ExperimentalFoundationApi::class)
internal fun HomePlanCard(plan: HomePlan, state: HomeState, viewModel: HomeViewModel) {
    var expanded by rememberSaveable { mutableStateOf(true) }
    var editingDeparture by rememberSaveable { mutableStateOf(false) }
    val inspectionAnchor = remember { BringIntoViewRequester() }
    val rainAnchor = remember { BringIntoViewRequester() }

    LaunchedEffect(state.inspection) {
        if (expanded && state.inspection != null) {
            withFrameNanos { }
            inspectionAnchor.bringIntoView()
        }
    }
    LaunchedEffect(state.rain) {
        if (expanded && state.rain != null) {
            withFrameNanos { }
            rainAnchor.bringIntoView()
        }
    }

    Card(modifier = Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(plan.summary.title, style = MaterialTheme.typography.titleLarge)
            Text("${plan.summary.date} · 版本 ${plan.summary.version}", style = MaterialTheme.typography.bodySmall)
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                TextButton(onClick = { expanded = !expanded }) { Text(if (expanded) "收起" else "展开") }
                TextButton(onClick = viewModel::closePlan, enabled = !state.planBusy) { Text("关闭预览") }
            }
            if (state.planBusy) LinearProgressIndicator(modifier = Modifier.fillMaxWidth())
            if (!expanded) return@Column

            plan.summary.nodes.forEach { stop ->
                val latest = stop.window?.latest
                Text(if (latest == null) stop.name else "${stop.name} · 最晚 $latest")
            }
            Row(horizontalArrangement = Arrangement.spacedBy(4.dp), verticalAlignment = Alignment.CenterVertically) {
                Text("试算出发时间 ${formatMinute(state.departureMinute)}", Modifier.weight(1f))
                TextButton(onClick = { editingDeparture = true }, enabled = !state.planBusy) { Text("修改出发时间") }
            }
            Text("试算设置仅用于当前预览。", style = MaterialTheme.typography.bodySmall)
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Button(onClick = viewModel::savePlan, enabled = !state.planBusy && !state.saved) {
                    Text(if (state.saved) "已保存" else "保存计划")
                }
                Button(onClick = viewModel::inspectPlan, enabled = !state.planBusy) { Text("体检") }
            }
            state.inspection?.let { inspection ->
                InspectionResults(
                    result = inspection,
                    plan = plan.summary,
                    departure = state.departureMinute,
                    busy = state.planBusy,
                    onUseRecommendation = viewModel::followDepartureRecommendation,
                    modifier = Modifier.bringIntoViewRequester(inspectionAnchor),
                )
            }

            Text("降雨模拟", style = MaterialTheme.typography.titleMedium)
            Text("按手动设定的首段延误试算。", style = MaterialTheme.typography.bodySmall)
            Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                rainDelayChoices.forEach { minutes ->
                    FilterChip(
                        selected = state.rainDelayMinutes == minutes,
                        onClick = { viewModel.changeRainDelay(minutes) },
                        enabled = !state.planBusy,
                        label = { Text("$minutes 分钟") },
                    )
                }
            }
            Button(onClick = viewModel::simulateRain, enabled = !state.planBusy) { Text("模拟降雨") }
            state.rain?.let { comparison ->
                RainResults(
                    comparison = comparison,
                    plan = plan.summary,
                    state = state,
                    onTryEarlier = viewModel::simulateRainEarlier,
                    modifier = Modifier.bringIntoViewRequester(rainAnchor),
                )
            }
        }
    }

    if (editingDeparture) {
        DepartureDialog(
            initialMinute = state.departureMinute,
            onDismiss = { editingDeparture = false },
            onConfirm = { minute ->
                viewModel.changeDeparture(minute)
                editingDeparture = false
            },
        )
    }
}

@Composable
private fun DepartureDialog(initialMinute: Int, onDismiss: () -> Unit, onConfirm: (Int) -> Unit) {
    var text by rememberSaveable(initialMinute) { mutableStateOf(formatMinute(initialMinute)) }
    val minute = parseDepartureMinute(text)
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("修改出发时间") },
        text = {
            OutlinedTextField(
                value = text,
                onValueChange = { text = it },
                label = { Text("出发时间（24 小时制）") },
                supportingText = { Text("例如 07:30") },
                singleLine = true,
                isError = minute == null,
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Ascii),
            )
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("取消") } },
        confirmButton = {
            TextButton(onClick = { minute?.let(onConfirm) }, enabled = minute != null) { Text("应用时间") }
        },
    )
}

@Composable
private fun InspectionResults(
    result: PlanInspection,
    plan: PlanSummary,
    departure: Int,
    busy: Boolean,
    onUseRecommendation: () -> Unit,
    modifier: Modifier = Modifier,
) {
    Text(
        "${formatMinute(departure)} 出发 · 达成把握 ${formatProbability(result.probability)}",
        modifier = modifier,
        style = MaterialTheme.typography.titleSmall,
    )
    val target = result.targetProbability ?: plan.targetProbability
    target?.let {
        val assessment = if (result.probability >= it) "达到目标" else "低于目标"
        Text("$assessment（${formatProbability(it)}）")
    }
    val suggestedMinute = result.departBy?.let(::parseDepartureMinute)
    if (suggestedMinute == null) {
        Text("未找到满足目标把握的出发时间")
    } else {
        Text("建议最晚 ${formatMinute(suggestedMinute)} 出发")
        if (suggestedMinute < departure) {
            TextButton(onClick = onUseRecommendation, enabled = !busy) { Text("按建议调整并重算") }
        }
    }
    plan.nodes.firstOrNull { it.id == result.weakest?.node }?.let { Text("最薄弱环节是${it.name}") }
}

@Composable
private fun RainResults(
    comparison: RainComparison,
    plan: PlanSummary,
    state: HomeState,
    onTryEarlier: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val before = formatProbability(comparison.before.probability)
    val after = formatProbability(comparison.after.probability)
    Text(
        "${formatMinute(state.departureMinute)} 出发 · 首段延误 ${state.rainDelayMinutes} 分钟",
        style = MaterialTheme.typography.bodySmall,
    )
    Text("达成把握 $before → $after", modifier = modifier, style = MaterialTheme.typography.titleSmall)
    if (comparison.affected.isEmpty()) Text("引擎未报告受影响的环节")
    comparison.affected.forEach { impact ->
        val name = plan.nodes.firstOrNull { it.id == impact.node }?.name ?: impact.node
        val previous = impact.before.arrivalP90.roundToInt()
        val updated = impact.after.arrivalP90.roundToInt()
        val difference = updated - previous
        val change = when {
            difference > 0 -> "，推迟 $difference 分钟"
            difference < 0 -> "，提前 ${-difference} 分钟"
            else -> ""
        }
        Text("$name · 较保守到达 ${formatMinute(previous)} → ${formatMinute(updated)}$change")
    }
    if (comparison.options.isEmpty()) {
        Text("当前没有达到目标把握的方案")
        if (state.departureMinute >= EARLIER_RAIN_RECHECK_MINUTES) {
            TextButton(onClick = onTryEarlier, enabled = !state.planBusy) {
                Text("提前 $EARLIER_RAIN_RECHECK_MINUTES 分钟再模拟")
            }
        } else {
            Text("可调整试算设置，或核对是否能调整活动时间。")
        }
    }
    comparison.options.forEachIndexed { index, option ->
        val label = if (option.id == "keep_route") "可保留原路线" else "调整方案 ${index + 1}"
        Text("$label · 达成把握 ${formatProbability(option.sim.probability)}")
    }
}

private fun formatProbability(value: Double) = NumberFormat.getPercentInstance().apply {
    maximumFractionDigits = 2
}.format(value)

private fun formatMinute(minute: Int): String {
    val day = Math.floorDiv(minute, 1440)
    val time = LocalTime.ofSecondOfDay(Math.floorMod(minute, 1440) * 60L)
    return if (day == 0) time.toString() else "第 ${day + 1} 天 $time"
}
