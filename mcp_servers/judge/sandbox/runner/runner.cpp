#include <chrono>
#include <csignal>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fcntl.h>
#include <fstream>
#include <iostream>
#include <map>
#include <string>
#include <sys/resource.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <thread>
#include <unistd.h>

using Clock = std::chrono::steady_clock;

static std::map<std::string, std::string> parse_args(int argc, char** argv) {
    std::map<std::string, std::string> result;
    for (int i = 1; i + 1 < argc; i += 2) result[argv[i]] = argv[i + 1];
    return result;
}

static bool copy_file(const std::string& from, const std::string& to) {
    std::ifstream input(from, std::ios::binary);
    std::ofstream output(to, std::ios::binary);
    output << input.rdbuf();
    return input.good() || input.eof();
}

static void set_limit(int resource, rlim_t value) {
    struct rlimit limit { value, value };
    setrlimit(resource, &limit);
}

int main(int argc, char** argv) {
    auto args = parse_args(argc, argv);
    const long time_ms = std::stol(args["--time-ms"]);
    const long memory_mb = std::stol(args["--memory-mb"]);
    const long output_bytes = std::stol(args["--output-bytes"]);
    copy_file(args["--stdin"], args["--problem-input"]);
    auto started = Clock::now();
    pid_t pid = fork();
    if (pid == 0) {
        setpgid(0, 0);
        chdir("/work");
        int input = open(args["--stdin"].c_str(), O_RDONLY);
        int output = open(args["--stdout"].c_str(), O_WRONLY | O_CREAT | O_TRUNC, 0644);
        int error = open(args["--stderr"].c_str(), O_WRONLY | O_CREAT | O_TRUNC, 0644);
        dup2(input, STDIN_FILENO); dup2(output, STDOUT_FILENO); dup2(error, STDERR_FILENO);
        close(input); close(output); close(error);
        set_limit(RLIMIT_AS, static_cast<rlim_t>(memory_mb) * 1024 * 1024);
        set_limit(RLIMIT_FSIZE, static_cast<rlim_t>(output_bytes));
        set_limit(RLIMIT_NOFILE, 64);
        set_limit(RLIMIT_NPROC, 32);
        set_limit(RLIMIT_STACK, 64 * 1024 * 1024);
        set_limit(RLIMIT_CPU, static_cast<rlim_t>((time_ms + 999) / 1000 + 1));
        execl(args["--executable"].c_str(), args["--executable"].c_str(), static_cast<char*>(nullptr));
        _exit(127);
    }
    setpgid(pid, pid);
    int status = 0;
    bool timed_out = false;
    struct rusage usage {};
    while (true) {
        pid_t done = wait4(pid, &status, WNOHANG, &usage);
        if (done == pid) break;
        if (done < 0) break;
        auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(Clock::now() - started).count();
        if (elapsed > time_ms) {
            timed_out = true;
            kill(-pid, SIGKILL);
            wait4(pid, &status, 0, &usage);
            break;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(5));
    }
    auto wall_ms = std::chrono::duration_cast<std::chrono::milliseconds>(Clock::now() - started).count();
    if (access(args["--file-output"].c_str(), F_OK) == 0) copy_file(args["--file-output"], args["--copied-file-output"]);
    long cpu_ms = usage.ru_utime.tv_sec * 1000L + usage.ru_utime.tv_usec / 1000L + usage.ru_stime.tv_sec * 1000L + usage.ru_stime.tv_usec / 1000L;
    int exit_code = WIFEXITED(status) ? WEXITSTATUS(status) : 128 + (WIFSIGNALED(status) ? WTERMSIG(status) : 0);
    bool memory_limited = WIFSIGNALED(status) && (WTERMSIG(status) == SIGKILL || WTERMSIG(status) == SIGSEGV) && usage.ru_maxrss >= memory_mb * 900;
    bool output_limited = WIFSIGNALED(status) && WTERMSIG(status) == SIGXFSZ;
    std::ofstream stats(args["--stats"]);
    stats << "{\"exit_code\":" << exit_code
          << ",\"wall_ms\":" << wall_ms
          << ",\"cpu_ms\":" << cpu_ms
          << ",\"peak_rss_kb\":" << usage.ru_maxrss
          << ",\"timed_out\":" << (timed_out ? "true" : "false")
          << ",\"memory_limited\":" << (memory_limited ? "true" : "false")
          << ",\"output_limited\":" << (output_limited ? "true" : "false") << "}\n";
    return 0;
}

