package com.suibian.app

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import com.suibian.app.ui.home.HomeScreen
import com.suibian.app.ui.theme.SuibianTheme
import dagger.hilt.android.AndroidEntryPoint

@AndroidEntryPoint
class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            SuibianTheme {
                HomeScreen()
            }
        }
    }
}
