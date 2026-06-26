package com.example;

import java.lang.reflect.Method;
import java.nio.file.Files;
import java.nio.file.Path;

public final class GeorgeProfile {
    private static final String CLASS_NAME = "jdk.internal.profilecheckpoint.ProfileCheckpoint";

    private GeorgeProfile() {}

    public static void loadIfPresent(Path path) {
        try {
            if (!Files.exists(path) || Files.size(path) == 0) {
                System.out.println("GEORGE_LOAD_SKIP reason=missing");
                return;
            }
            Class<?> cls = Class.forName(CLASS_NAME);
            Method load = cls.getDeclaredMethod("load", Path.class);
            load.invoke(null, path);
            System.out.println("GEORGE_LOAD_OK path=" + path + " bytes=" + Files.size(path));
        } catch (Throwable t) {
            System.out.println("GEORGE_LOAD_FAIL " + t);
            t.printStackTrace(System.out);
        }
    }

    public static void dumpBestEffort(Path path) {
        try {
            Files.createDirectories(path.getParent());
            Class<?> cls = Class.forName(CLASS_NAME);
            Method dump = cls.getDeclaredMethod("dump", Path.class);
            dump.invoke(null, path);
            long bytes = Files.exists(path) ? Files.size(path) : -1;
            System.out.println("GEORGE_DUMP_OK path=" + path + " bytes=" + bytes);
        } catch (Throwable t) {
            System.out.println("GEORGE_DUMP_FAIL " + t);
            t.printStackTrace(System.out);
        }
    }
}
