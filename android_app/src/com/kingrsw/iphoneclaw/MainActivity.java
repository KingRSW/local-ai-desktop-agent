package com.kingrsw.iphoneclaw;

import android.app.Activity;
import android.app.AlarmManager;
import android.app.AlertDialog;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.TimePickerDialog;
import android.content.ContentValues;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.database.Cursor;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.provider.CalendarContract;
import android.view.Menu;
import android.view.MenuItem;
import android.view.View;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.EditText;
import android.widget.Toast;

import java.util.Calendar;

public class MainActivity extends Activity {
    private static final String PREFS = "iphoneclaw";
    private static final String KEY_URL = "server_url";
    static final String CHANNEL = "iphoneclaw_alarm";
    private static final int REQ_NOTIFY = 1, REQ_CAL = 2;
    private WebView web;

    @Override
    protected void onCreate(Bundle b) {
        super.onCreate(b);
        setContentView(R.layout.activity_main);
        ensureChannel();
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
        if (url.isEmpty()) askUrl(sp, true); else web.loadUrl(url);
    }

    private void ensureChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            NotificationManager nm = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
            if (nm.getNotificationChannel(CHANNEL) == null) {
                NotificationChannel c = new NotificationChannel(CHANNEL, "iPhoneClaw 闹钟", NotificationManager.IMPORTANCE_HIGH);
                nm.createNotificationChannel(c);
            }
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
                if (!u.isEmpty()) { sp.edit().putString(KEY_URL, u).apply(); web.loadUrl(u); }
            })
            .setNegativeButton("取消", null).show();
    }

    @Override public void onBackPressed() { if (web.canGoBack()) web.goBack(); else super.onBackPressed(); }
    public void onChangeUrl(View v) { askUrl(getSharedPreferences(PREFS, MODE_PRIVATE), false); }

    private void showAlarmDialog() {
        Calendar now = Calendar.getInstance();
        new TimePickerDialog(this, (view, h, m) -> {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
                checkSelfPermission(android.Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
                requestPermissions(new String[]{android.Manifest.permission.POST_NOTIFICATIONS}, REQ_NOTIFY);
            }
            EditText titleEt = new EditText(this);
            titleEt.setHint("闹钟标题，如 起床");
            new AlertDialog.Builder(this).setTitle("闹钟标题").setView(titleEt)
                .setPositiveButton("确定", (d, w) -> setAlarm(h, m, titleEt.getText().toString().trim())).show();
        }, now.get(Calendar.HOUR_OF_DAY), now.get(Calendar.MINUTE), true).show();
    }

    private void setAlarm(int h, int m, String title) {
        Calendar cal = Calendar.getInstance();
        cal.set(Calendar.HOUR_OF_DAY, h); cal.set(Calendar.MINUTE, m); cal.set(Calendar.SECOND, 0);
        if (cal.getTimeInMillis() <= System.currentTimeMillis()) cal.add(Calendar.DATE, 1);
        AlarmManager am = (AlarmManager) getSystemService(ALARM_SERVICE);
        Intent it = new Intent(this, AlarmReceiver.class);
        it.putExtra("title", title.isEmpty() ? "iPhoneClaw 闹钟" : title);
        PendingIntent pi = PendingIntent.getBroadcast(this, (int)(System.currentTimeMillis() & 0xffff), it,
                PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
        try {
            am.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, cal.getTimeInMillis(), pi);
            Toast.makeText(this, "✅ 已设闹钟 " + String.format("%02d:%02d", h, m), Toast.LENGTH_SHORT).show();
        } catch (SecurityException e) {
            Toast.makeText(this, "❌ 需要精确闹钟权限（设置里授予）", Toast.LENGTH_LONG).show();
        }
    }

    private void showCalendarDialog() {
        if (checkSelfPermission(android.Manifest.permission.WRITE_CALENDAR) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{android.Manifest.permission.READ_CALENDAR, android.Manifest.permission.WRITE_CALENDAR}, REQ_CAL);
        }
        Calendar now = Calendar.getInstance();
        EditText titleEt = new EditText(this);
        titleEt.setHint("日程标题，如 开会");
        new TimePickerDialog(this, (view, h, m) -> {
            new AlertDialog.Builder(this).setTitle("日程标题").setView(titleEt)
                .setPositiveButton("确定", (d, w) -> addCalendar(h, m, titleEt.getText().toString().trim())).show();
        }, now.get(Calendar.HOUR_OF_DAY), now.get(Calendar.MINUTE), true).show();
    }

    private void addCalendar(int h, int m, String title) {
        long calId = getDefaultCalendarId();
        if (calId < 0) { Toast.makeText(this, "❌ 未找到可用日历账户", Toast.LENGTH_LONG).show(); return; }
        Calendar cal = Calendar.getInstance();
        cal.set(Calendar.HOUR_OF_DAY, h); cal.set(Calendar.MINUTE, m); cal.set(Calendar.SECOND, 0);
        long start = cal.getTimeInMillis();
        long end = start + 3600_000L;
        ContentValues v = new ContentValues();
        v.put(CalendarContract.Events.CALENDAR_ID, calId);
        v.put(CalendarContract.Events.TITLE, title.isEmpty() ? "iPhoneClaw 日程" : title);
        v.put(CalendarContract.Events.DTSTART, start);
        v.put(CalendarContract.Events.DTEND, end);
        v.put(CalendarContract.Events.EVENT_TIMEZONE, Calendar.getInstance().getTimeZone().getID());
        try {
            getContentResolver().insert(CalendarContract.Events.CONTENT_URI, v);
            Toast.makeText(this, "✅ 已加到日历", Toast.LENGTH_SHORT).show();
        } catch (Exception e) {
            Toast.makeText(this, "❌ 日历写入失败：" + e.getMessage(), Toast.LENGTH_LONG).show();
        }
    }

    private long getDefaultCalendarId() {
        try (Cursor c = getContentResolver().query(CalendarContract.Calendars.CONTENT_URI,
                new String[]{CalendarContract.Calendars._ID},
                CalendarContract.Calendars.VISIBLE + "=1 AND " + CalendarContract.Calendars.IS_PRIMARY + "=1", null, null)) {
            if (c != null && c.moveToFirst()) return c.getLong(0);
        } catch (Exception ignore) {}
        try (Cursor c = getContentResolver().query(CalendarContract.Calendars.CONTENT_URI,
                new String[]{CalendarContract.Calendars._ID}, CalendarContract.Calendars.VISIBLE + "=1", null, null)) {
            if (c != null && c.moveToFirst()) return c.getLong(0);
        } catch (Exception ignore) {}
        return -1;
    }

    @Override public boolean onCreateOptionsMenu(Menu menu) {
        menu.add(0, 1, 0, "⏰ 离线闹钟");
        menu.add(0, 2, 0, "📅 离线日历");
        menu.add(0, 3, 0, "🔗 改地址");
        return true;
    }
    @Override public boolean onOptionsItemSelected(MenuItem item) {
        if (item.getItemId() == 1) showAlarmDialog();
        else if (item.getItemId() == 2) showCalendarDialog();
        else if (item.getItemId() == 3) onChangeUrl(null);
        return true;
    }

    @Override public void onRequestPermissionsResult(int req, String[] perms, int[] res) {
        super.onRequestPermissionsResult(req, perms, res);
    }
}
