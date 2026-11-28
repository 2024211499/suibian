package com.suibian.app.deployment

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.suibian.app.executor.ActionType
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter

@Composable
internal fun DeploymentHistoryScreen(state: DeploymentHistory, viewModel: DeploymentViewModel) {
    var confirmingUndo by rememberSaveable { mutableStateOf<Long?>(null) }
    val latestCalendarId = state.rows.firstOrNull {
        it.batchId == "deployment:${state.version}:${state.planId}" && it.actionType == ActionType.CALENDAR_WRITE.name
    }?.id

    BackHandler(enabled = confirmingUndo == null) { viewModel.closeHistory() }
    LazyColumn(Modifier.fillMaxSize().padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        item {
            TextButton(onClick = viewModel::closeHistory, enabled = !state.busy) { Text("返回") }
            Text("操作记录", style = MaterialTheme.typography.headlineSmall)
            state.message?.let { Text(it) }
            if (state.rows.isEmpty()) Text("此计划还没有部署记录")
        }
        items(state.rows, key = { it.id }) { row ->
            val type = ActionType.entries.firstOrNull { it.name == row.actionType }
            Card(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    Text("${type?.let(::deploymentActionLabel) ?: row.actionType} · ${deploymentDecisionLabel(row.decision)}")
                    Text(Instant.ofEpochMilli(row.ts).atZone(ZoneId.systemDefault()).format(LoggedAtFormat),
                        style = MaterialTheme.typography.bodySmall)
                    deploymentDetail(row)?.takeIf { it.isNotBlank() }?.let { Text(it) }
                    if (row.id == latestCalendarId && row.decision in setOf("done", "undo_pending") && row.undoData.isNotBlank()) {
                        TextButton(onClick = { confirmingUndo = row.id }, enabled = !state.busy) {
                            Text(if (row.decision == "undo_pending") "继续撤销日历" else "撤销日历事件")
                        }
                    }
                }
            }
        }
    }

    confirmingUndo?.let { recordId ->
        AlertDialog(
            onDismissRequest = { confirmingUndo = null },
            title = { Text("撤销这条日历事件？") },
            text = { Text("只撤销随变写入的这条日历事件。App 内计划和出门提醒会保留。") },
            dismissButton = { TextButton(onClick = { confirmingUndo = null }) { Text("返回") } },
            confirmButton = { Button(onClick = {
                confirmingUndo = null
                viewModel.undoCalendar(recordId)
            }) { Text("确认撤销") } },
        )
    }
}

private val LoggedAtFormat = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm")
