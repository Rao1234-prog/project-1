/*
 * JARVIS.app bundle launcher.
 *
 * A tiny compiled Mach-O so the .app has a REAL, code-signable application
 * identity (com.omkaar.jarvis) that macOS TCC can attribute Accessibility and
 * Screen Recording to. A shell-script executable doesn't work: the running
 * Mach-O is /bin/bash (Apple's identity), so TCC can't match the bundle grant.
 *
 * It runs the venv Python as a CHILD (fork/exec, not a replacing exec) and
 * waits. This process stays alive as the "responsible process", exactly like
 * Terminal did in manual launch — so the Python child inherits the bundle's
 * Accessibility grant. Termination signals are forwarded so launchd's KeepAlive
 * never leaves an orphaned duplicate behind.
 *
 * Paths are derived from the executable's own location, so the bundle is
 * relocatable and contains no hard-coded user paths.
 */
#include <limits.h>
#include <mach-o/dyld.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

static pid_t child = 0;

static void forward(int sig) {
    if (child > 0) kill(child, sig);
}

int main(void) {
    char exe[PATH_MAX];
    uint32_t sz = sizeof(exe);
    if (_NSGetExecutablePath(exe, &sz) != 0) return 1;

    char repo[PATH_MAX];
    if (!realpath(exe, repo)) return 1;
    /* repo = <REPO>/JARVIS.app/Contents/MacOS/jarvis  -> strip 4 components */
    for (int i = 0; i < 4; i++) {
        char *slash = strrchr(repo, '/');
        if (!slash) return 1;
        *slash = '\0';
    }

    char pythonpath[PATH_MAX], python[PATH_MAX];
    snprintf(pythonpath, sizeof(pythonpath), "%s/src", repo);
    snprintf(python, sizeof(python), "%s/.venv/bin/python", repo);
    setenv("PYTHONPATH", pythonpath, 1);

    child = fork();
    if (child == 0) {
        execl(python, "python", "-m", "jarvis.main", (char *)NULL);
        _exit(127);  /* exec failed */
    }
    if (child < 0) return 1;

    signal(SIGTERM, forward);
    signal(SIGINT, forward);

    int status;
    while (waitpid(child, &status, 0) < 0) { /* retry on EINTR */ }
    return 0;
}
