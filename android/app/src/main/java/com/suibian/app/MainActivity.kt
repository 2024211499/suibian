package com.suibian.app

import android.os.Bundle
import android.util.Log
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.lifecycle.lifecycleScope
import com.suibian.app.background.ReminderDelivery
import com.suibian.app.ui.home.HomeScreen
import com.suibian.app.ui.theme.SuibianTheme
import dagger.hilt.android.AndroidEntryPoint
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.launch
import javax.inject.Inject

@AndroidEntryPoint
class MainActivity : ComponentActivity() {
    @Inject lateinit var reminderDelivery: ReminderDelivery

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            SuibianTheme {
                HomeScreen()
            }
        }
    }

    override fun onResume() {
        super.onResume()
        lifecycleScope.launch {
            try {
                reminderDelivery.restore()
            } catch (e: CancellationException) {
                throw e
            } catch (e: RuntimeException) {
                Log.w("SuibianAlarm", "Could not reconcile reminders on resume", e)
            }
        }
    }
}
