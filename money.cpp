#include <iostream>
#include <vector>
#include <algorithm>
#include <cstdio>

using namespace std;

const int MAX_A = 25000; // [STEP S1] maximum denomination per constraints

void solve() {
    int T;
    // [STEP S1] read number of test cases
    if (!(cin >> T)) return;
    while (T--) {
        int n;
        // [STEP S1] read n
        cin >> n;
        vector<int> a(n);
        // [STEP S1] read denominations
        for (int i = 0; i < n; ++i) cin >> a[i];

        // [STEP S2] sort denominations ascending
        sort(a.begin(), a.end());

        // [STEP S3] initialize DP boolean array for representable sums
        vector<bool> dp(MAX_A + 1, false);
        dp[0] = true;

        int ans = 0;
        // [STEP S4] greedy scan: keep if not representable by previous kept
        for (int x : a) {
            if (!dp[x]) {
                ans++;
                // [STEP S4] unbounded knapsack update: mark all sums reachable with x
                for (int i = x; i <= MAX_A; ++i) {
                    if (dp[i - x]) dp[i] = true;
                }
            }
            // else redundant, skip
        }

        // [STEP S5] output minimal m for this test case
        cout << ans << '\n';
    }
}

int main() {
    // [STEP S1] file I/O redirection per NOIP convention
    freopen("money.in", "r", stdin);
    freopen("money.out", "w", stdout);
    ios::sync_with_stdio(false);
    cin.tie(nullptr);
    solve();
    return 0;
}