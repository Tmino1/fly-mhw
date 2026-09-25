using SharpPluginLoader.Core;
using SharpPluginLoader.Core.IO;

namespace QuestStartTrigger;

/// <summary>
/// Two ways to start a quest without walking to the Handler and clicking
/// through the departure prompt: an in-game F8 hotkey (fixed to this
/// project's one pilot quest id, 90099 — a hotkey press carries no data),
/// or a flag file (same directory as fly_mhw_state.json / the game's own
/// working directory — confirmed relative writes land there, see
/// docs/modding_setup.md) for a scripted/automated trigger with an
/// arbitrary quest id. Both call the same AcceptQuest + DepartOnQuest
/// chain. Motivation (2026-09-19): needed for eventually leaving RL
/// training unattended — episode resets can't depend on a human walking
/// anywhere; F8 added the same day for convenience during manual IL
/// recording sessions, so this doesn't require alt-tabbing to a terminal.
///
/// Mirrors state_reader.lua's post-hunt-wait timer-skip flag exactly: only
/// ever touches game memory in response to one of these two explicit
/// triggers, so normal untracked play is completely unaffected. See
/// scripts/trigger_quest_start.py for the Python side that writes the flag.
///
/// Both functions' real signatures (Quest.cs, SharpPluginLoader's own
/// source): `void AcceptQuest(nint questMgr, int questId, bool unk)`,
/// `void DepartOnQuest(nint questMgr, bool unk)`. questMgr's value is an
/// INFERENCE, not confirmed by SharpPluginLoader's own docs — every
/// quest-related hook in Quest.cs takes the same first parameter and none
/// reference any singleton besides sQuest, so questMgr ==
/// Quest.SingletonInstance.Instance (the same pointer state_reader.lua
/// already resolves for the timer skip) is reasonable. **Confirmed correct
/// for BOTH calls, live, 2026-09-19**: quest.state went accepted (1) ->
/// in-hunt (2), player position genuinely changed (teleported into the
/// arena map) — no crash, on either call.
/// </summary>
public class Plugin : IPlugin
{
    public string Name => "QuestStartTrigger";
    public string Author => "fly-mhw";

    private const string FlagFileName = "fly_mhw_start_quest.flag";

    // Fixed to this project's one pilot quest (docs/architecture.md:
    // "Target pair: Great Jagras, with the Great Sword", arena quest
    // built in scripts/patch_arena_quest.py) — the F8 hotkey has no way
    // to carry an arbitrary id the way the flag file does.
    private const int HotkeyQuestId = 90099;

    // Both addresses read directly from this machine's
    // nativePC/plugins/CSharp/Loader/NativeAddressCache.json (the exact
    // values SharpPluginLoader's own AOB scan produced the last time it ran
    // against this game binary — AddressRepository itself is `internal` to
    // SharpPluginLoader.Core, inaccessible from an external plugin
    // assembly, confirmed by a real CS0122 build failure). Machine/game-
    // version-specific: if the game updates, re-derive from that same
    // cache file after SharpPluginLoader has run once against the new
    // binary.
    // unchecked: both exceed int32 range, which trips the compiler's
    // conservative nint-overflow check even though this always runs as a
    // 64-bit process (the game itself, .NET 8 x64) where it fits easily.
    private static readonly nint AcceptQuestAddress = unchecked((nint)0x141b659b0);
    private static readonly nint DepartOnQuestAddress = unchecked((nint)0x141b69f10);

    private NativeAction<nint, int, bool> _acceptQuest = default;
    private NativeAction<nint, bool> _departOnQuest = default;

    // Once AcceptQuest is called, wait for Quest.QuestState to actually
    // read 1 (accepted) before calling DepartOnQuest — calling it before
    // the accept has genuinely landed would be a second unconfirmed
    // native call layered on an unconfirmed game state, harder to
    // interpret if something goes wrong. Time-bounded so a failed/ignored
    // accept can't permanently wedge this plugin out of ever responding
    // to a future trigger.
    private bool _departPending = false;
    private int _pendingQuestId = -1;
    private float _departWaitElapsedSeconds = 0f;
    private const float DepartWaitTimeoutSeconds = 10f;

    public void OnLoad()
    {
        _acceptQuest = new NativeAction<nint, int, bool>(AcceptQuestAddress);
        _departOnQuest = new NativeAction<nint, bool>(DepartOnQuestAddress);
        // Plain ASCII hyphen, not an em dash — SharpPluginLoader.log's
        // writer mangled a non-ASCII character here (cosmetic only).
        Log.Info($"QuestStartTrigger loaded - F8 starts quest {HotkeyQuestId}, or watching for {FlagFileName}");
    }

    public unsafe void OnUpdate(float deltaTime)
    {
        if (_departPending)
        {
            _departWaitElapsedSeconds += deltaTime;

            if (Quest.CurrentQuestId == _pendingQuestId && Quest.QuestState == 1)
            {
                Log.Info($"QuestStartTrigger: quest {_pendingQuestId} reached state=1, calling DepartOnQuest");
                try
                {
                    _departOnQuest.Invoke(Quest.SingletonInstance.Instance, false);
                }
                catch (Exception ex)
                {
                    Log.Error($"QuestStartTrigger: exception calling DepartOnQuest: {ex}");
                }
                _departPending = false;
            }
            else if (_departWaitElapsedSeconds > DepartWaitTimeoutSeconds)
            {
                Log.Error($"QuestStartTrigger: gave up waiting for quest {_pendingQuestId} to reach state=1 " +
                          $"after {DepartWaitTimeoutSeconds}s (currentId={Quest.CurrentQuestId}, " +
                          $"state={Quest.QuestState}) — not calling DepartOnQuest");
                _departPending = false;
            }
            // Don't also check for a new trigger this same tick.
            return;
        }

        if (Input.IsPressed(Key.F8))
        {
            StartQuest(HotkeyQuestId);
            return;
        }

        if (!File.Exists(FlagFileName))
        {
            return;
        }

        try
        {
            var text = File.ReadAllText(FlagFileName).Trim();
            // Delete the flag BEFORE calling AcceptQuest, not after — if the
            // call itself crashes the process, a flag left behind would
            // otherwise refire the exact same (already-suspect) call on the
            // very next launch.
            File.Delete(FlagFileName);

            if (!int.TryParse(text, out var questId))
            {
                Log.Error($"QuestStartTrigger: {FlagFileName} contained non-integer content '{text}', ignoring");
                return;
            }

            StartQuest(questId);
        }
        catch (Exception ex)
        {
            // Won't catch a hard native crash from the call itself, but
            // covers everything else (file races, bad content) rather than
            // silently doing nothing.
            Log.Error($"QuestStartTrigger: exception handling {FlagFileName}: {ex}");
        }
    }

    private unsafe void StartQuest(int questId)
    {
        Log.Info($"QuestStartTrigger: calling AcceptQuest(questId={questId})");
        _acceptQuest.Invoke(Quest.SingletonInstance.Instance, questId, false);

        _pendingQuestId = questId;
        _departWaitElapsedSeconds = 0f;
        _departPending = true;
    }
}
