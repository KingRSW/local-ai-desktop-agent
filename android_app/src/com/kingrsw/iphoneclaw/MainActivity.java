package com.kingrsw.iphoneclaw;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.SharedPreferences;
import android.os.Bundle;
import android.view.View;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.EditText;

public class MainActivity extends Activity {
    private static final String PREFS = "iphoneclaw";
    private static final String KEY_URL = "server_url";
    private WebView web;

    @Override
    protected void onCreate(Bundle b) {
        super.onCreate(b);
        setContentView(R.layout.activity_main);
        web = findViewById(R.id.web);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setLoadWithOverviewMode(true);
        s.setUseWideViewPort(true);
        s.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);
        web.setWebViewClient(new WebViewClient() {
            @Override public boolean shouldOverrideUrlLoading(android.webkit.WebView v, String url) {
                v.loadUrl(url); return true;
            }
        });

        SharedPreferences sp = getSharedPreferences(PREFS, MODE_PRIVATE);
        String url = sp.getString(KEY_URL, "");
        if (url.isEmpty()) {
            askUrl(sp, true);
        } else {
            web.loadUrl(url);
        }
    }

    private void askUrl(SharedPreferences sp, boolean first) {
        EditText et = new EditText(this);
        et.setHint("https://xxxx.trycloudflare.com 或 http://192.168.1.x:8742");
        et.setText(sp.getString(KEY_URL, ""));
        new AlertDialog.Builder(this)
            .setTitle(first ? "iPhoneClaw · 填服务器地址" : "改服务器地址")
            .setMessage("Mac 端启动 remote_tunnel.sh 或 server.py 后，把地址填这里。")
            .setView(et)
            .setPositiveButton("连接", (d, w) -> {
                String u = et.getText().toString().trim();
                if (!u.isEmpty()) {
                    sp.edit().putString(KEY_URL, u).apply();
                    web.loadUrl(u);
                }
            })
            .setNegativeButton("取消", null)
            .show();
    }

    @Override
    public void onBackPressed() {
        if (web.canGoBack()) web.goBack();
        else super.onBackPressed();
    }

    // 长按画面可重新填地址
    public void onChangeUrl(View v) {
        SharedPreferences sp = getSharedPreferences(PREFS, MODE_PRIVATE);
        askUrl(sp, false);
    }
}
