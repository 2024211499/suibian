package com.suibian.app.deployment

import android.Manifest
import android.content.pm.PackageManager
import android.os.Build
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import com.suibian.app.data.db.ActionLogEntity
import com.suibian.app.executor.ActionType
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive

@Composable
fun DeploymentScreen(state: DeploymentEditor, viewModel: DeploymentViewModel) {
    val context = LocalContext.current
    val calendarPermission = rememberLauncherForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) {
        viewModel.refreshCalendars()
    }
    var notificationGranted by rememberSaveable {
        mutableStateOf(Build.VERSION.SDK_INT < 33 ||
            context.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED)
    }
    val notificationPermission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) {
        notificationGranted = it
    }
    var showCalendars by rememberSaveable { mutableStateOf(false) }
    val selectedCalendar = state.calendars.firstOrNull { it.id == state.form.calendarId }
    val calendarLabel = selectedCalendar?.name ?: "选择目标日历"
    val calendarWritten = state.records.any { it.actionType == ActionType.CALENDAR_WRITE.name && it.decision == "done" }
    val reminderWritten = state.records.any { it.actionType == ActionType.ALARM_SET.name && it.decision == "done" }

    BackHandler(enabled = state.preview == null) { viewModel.close() }
    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        TextButton(onClick = viewModel::close, enabled = !state.busy) { Text("返回计划") }
        Text("部署计划", style = MaterialTheme.typography.headlineSmall)
        Text("版本 ${state.version}", style = MaterialTheme.typography.bodySmall)
        if (state.busy) LinearProgressIndicator(Modifier.fillMaxWidth())
        val canEditCalendar = !state.busy && !calendarWritten
        val canEditReminder = !state.busy && !reminderWritten
        val form = state.form
        FormField("活动标题", form.title, canEditCalendar, ascii = false) { value -> viewModel.changeForm { it.copy(title = value) } }
        FormField("活动日期（YYYY-MM-DD）", form.date, canEditCalendar && !reminderWritten) { value -> viewModel.changeForm { it.copy(date = value) } }
        FormField("时区", form.zone, canEditCalendar && !reminderWritten) { value -> viewModel.changeForm { it.copy(zone = value) } }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            FormField("开始时间（HH:MM）", form.start, canEditCalendar, Modifier.weight(1f)) { value -> viewModel.changeForm { it.copy(start = value) } }
            FormField("结束时间（HH:MM）", form.end, canEditCalendar, Modifier.weight(1f)) { value -> viewModel.changeForm { it.copy(end = value) } }
        }
        FormField("出门提醒（HH:MM）", form.reminder, canEditReminder) { value -> viewModel.changeForm { it.copy(reminder = value) } }
        if (!state.calendarPermission) {
            TextButton(
                onClick = { calendarPermission.launch(arrayOf(Manifest.permission.READ_CALENDAR, Manifest.permission.WRITE_CALENDAR)) },
                enabled = !state.busy,
            ) { Text("授权日历读写") }
        } else if (state.calendars.isEmpty()) {
            Text("没有可写日历，可先设置出门提醒")
            TextButton(onClick = viewModel::refreshCalendars, enabled = !state.busy) { Text("刷新可写日历") }
        } else {
            TextButton(onClick = { showCalendars = true }, enabled = canEditCalendar) { Text(calendarLabel) }
            DropdownMenu(expanded = showCalendars, onDismissRequest = { showCalendars = false }) {
                state.calendars.forEach { calendar ->
                    DropdownMenuItem(text = { Text(calendar.name) }, onClick = {
                        viewModel.changeForm { it.copy(calendarId = calendar.id) }
                        showCalendars = false
                    })
                }
            }
        }
        if (!notificationGranted && Build.VERSION.SDK_INT >= 33) {
            TextButton(onClick = { notificationPermission.launch(Manifest.permission.POST_NOTIFICATIONS) }, enabled = !state.busy) {
                Text("开启出门通知")
            }
        }
        state.message?.let { Text(it, color = MaterialTheme.colorScheme.error) }
        if (state.records.isNotEmpty()) {
            TextButton(onClick = viewModel::openEditorHistory, enabled = !state.busy) { Text("操作记录") }
        }
        Button(onClick = { viewModel.preparePreview() }, enabled = !state.busy && (!calendarWritten || !reminderWritten)) {
            Text("预览部署动作")
        }
        state.records.forEach { row ->
            val type = ActionType.valueOf(row.actionType)
            Text("${deploymentActionLabel(type)} · ${deploymentDecisionLabel(row.decision)}", style = MaterialTheme.typography.titleSmall)
            deploymentDetail(row)?.takeIf { it.isNotBlank() }?.let { Text(it) }
            if (row.decision in setOf("failed", "pending", "confirmed")) {
                TextButton(onClick = { viewModel.preparePreview(type) }, enabled = !state.busy) {
                    Text("只重试${deploymentActionLabel(type)}")
                }
            }
        }
    }

    state.preview?.let { review ->
        val shownForm = review.spec.toForm()
        AlertDialog(
            onDismissRequest = viewModel::dismissPreview,
            title = { Text("确认部署动作") },
            text = {
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text(shownForm.title)
                    Text("${shownForm.date} ${shownForm.start}–${shownForm.end} · ${shownForm.zone}")
                    if (review.only != ActionType.ALARM_SET) {
                        Text(if (selectedCalendar == null) "日历未选定，日历动作将标为未执行" else "写入一条日历事件 · ${selectedCalendar.name}")
                    }
                    if (review.only != ActionType.CALENDAR_WRITE) Text("出门提醒 ${shownForm.reminder}")
                }
            },
            dismissButton = { TextButton(onClick = viewModel::dismissPreview) { Text("取消部署") } },
            confirmButton = { Button(onClick = viewModel::confirm) { Text("确认部署") } },
        )
    }
}

@Composable
private fun FormField(
    label: String,
    value: String,
    enabled: Boolean,
    modifier: Modifier = Modifier.fillMaxWidth(),
    ascii: Boolean = true,
    onChange: (String) -> Unit,
) {
    OutlinedTextField(
        value, onChange, modifier, enabled = enabled, label = { Text(label) }, singleLine = true,
        keyboardOptions = KeyboardOptions(keyboardType = if (ascii) KeyboardType.Ascii else KeyboardType.Text),
    )
}

internal fun deploymentActionLabel(type: ActionType) = when (type) {
    ActionType.CALENDAR_WRITE -> "日历"
    ActionType.ALARM_SET -> "提醒"
    else -> type.name
}

internal fun deploymentDecisionLabel(decision: String) = when (decision) {
    "done" -> "已执行"
    "failed" -> "未执行，可重试"
    "confirmed" -> "执行结果待确认"
    "undo_pending" -> "撤销结果待确认"
    "undone" -> "已撤销"
    else -> "待执行"
}

internal fun deploymentDetail(row: ActionLogEntity): String? = runCatching {
    Json.parseToJsonElement(row.evidenceJson).jsonObject["detail"]?.jsonPrimitive?.content
}.getOrNull()
