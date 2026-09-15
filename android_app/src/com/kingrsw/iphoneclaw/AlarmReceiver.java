package com.kingrsw.iphoneclaw;

import android.app.Notification;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;

public class AlarmReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context ctx, Intent i) {
        String title = i.getStringExtra("title");
        if (title == null) title = "iPhoneClaw 闹钟";
        NotificationManager nm = (NotificationManager) ctx.getSystemService(Context.NOTIFICATION_SERVICE);
        if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.TIRAMISU &&
            ctx.checkSelfPermission(android.Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            return;
        }
        Intent open = ctx.getPackageManager().getLaunchIntentForPackage(ctx.getPackageName());
        PendingIntent pi = PendingIntent.getActivity(ctx, 0, open,
                PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
        Notification.Builder b = (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.O)
                ? new Notification.Builder(ctx, MainActivity.CHANNEL)
                : new Notification.Builder(ctx);
        b.setSmallIcon(android.R.drawable.ic_dialog_info)
                .setContentTitle("⏰ iPhoneClaw 闹钟")
                .setContentText(title)
                .setPriority(Notification.PRIORITY_HIGH)
                .setAutoCancel(true)
                .setContentIntent(pi);
        nm.notify((int)(System.currentTimeMillis() & 0xffff), b.build());
    }
}
