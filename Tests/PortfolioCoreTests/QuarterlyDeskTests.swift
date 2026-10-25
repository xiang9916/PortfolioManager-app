import XCTest
@testable import PortfolioCore

/// `QuarterlyDesk`：增删改的时序 + 0.6s 防抖落盘（尤其「删除必须取消未落盘快照」）。
@MainActor
final class QuarterlyDeskTests: XCTestCase {

    private struct Boom: Error {}

    private final class Recorder {
        var upserts: [[QuarterlyReport]] = []
        var removed: [String] = []
        var renames: [(String, String)] = []
        var failUpsert = false
        var failRemove = false
        var failRename = false
    }

    func testAddAppendsNextQuarterAndPersistsImmediately() {
        let recorder = Recorder()
        let desk = makeDesk(recorder)
        desk.replaceAll([report("2025-12-31")])

        XCTAssertEqual(desk.add(), .added(periodEnd: "2026-03-31"))
        XCTAssertEqual(desk.reports.map(\.periodEnd), ["2025-12-31", "2026-03-31"])
        XCTAssertEqual(recorder.upserts.count, 1, "结构性操作立即落盘")
        XCTAssertEqual(recorder.upserts.first?.map(\.periodEnd), ["2025-12-31", "2026-03-31"])
    }

    func testDeleteCancelsPendingDebounceAndRemovesRow() async {
        let recorder = Recorder()
        let desk = makeDesk(recorder)
        desk.replaceAll([report("2025-12-31"), report("2026-03-31")])

        desk.update(periodEnd: "2025-12-31", field: .marketValue, value: 42)
        XCTAssertEqual(desk.delete(periodEnd: "2025-12-31"), .deleted(periodEnd: "2025-12-31"))
        await Task.yield()
        await Task.yield()

        XCTAssertEqual(recorder.removed, ["2025-12-31"])
        XCTAssertTrue(recorder.upserts.isEmpty, "未落盘的旧快照不得把删掉的行写回来")
        XCTAssertEqual(desk.reports.map(\.periodEnd), ["2026-03-31"])
    }

    func testUpdateDebouncesToSingleUpsert() {
        let recorder = Recorder()
        let desk = makeDesk(recorder)
        desk.replaceAll([report("2025-12-31")])

        desk.update(periodEnd: "2025-12-31", field: .marketValue, value: 1)
        desk.update(periodEnd: "2025-12-31", field: .marketValue, value: 2)
        desk.flush()

        XCTAssertEqual(recorder.upserts.count, 1, "两次编辑只落一次盘")
        XCTAssertEqual(recorder.upserts.first?.first?.marketValue, 2)
    }

    func testRenameRejectsDuplicate() async {
        let recorder = Recorder()
        let desk = makeDesk(recorder)
        desk.replaceAll([report("2025-12-31"), report("2026-03-31")])

        XCTAssertEqual(desk.rename(from: "2026-03-31", to: "2025-12-31"),
                       .duplicate(periodEnd: "2025-12-31"))
        XCTAssertTrue(recorder.renames.isEmpty)
        XCTAssertEqual(desk.reports.map(\.periodEnd), ["2025-12-31", "2026-03-31"])
    }

    func testRenamePersistsSortsAndFlushes() {
        let recorder = Recorder()
        let desk = makeDesk(recorder)
        desk.replaceAll([report("2025-12-31"), report("2026-06-30")])

        XCTAssertEqual(desk.rename(from: "2026-06-30", to: "2025-06-30"),
                       .renamed(to: "2025-06-30"))
        XCTAssertEqual(recorder.renames.count, 1)
        XCTAssertEqual(desk.reports.map(\.periodEnd), ["2025-06-30", "2025-12-31"])
        XCTAssertEqual(recorder.upserts.count, 1)
    }

    func testRenameUnchangedIsNoop() {
        let recorder = Recorder()
        let desk = makeDesk(recorder)
        desk.replaceAll([report("2025-12-31")])
        XCTAssertEqual(desk.rename(from: "2025-12-31", to: "  2025-12-31 "), .unchanged)
        XCTAssertTrue(recorder.renames.isEmpty)
        XCTAssertTrue(recorder.upserts.isEmpty)
    }

    func testPersistFailureSurfaces() {
        let recorder = Recorder()
        recorder.failUpsert = true
        let desk = makeDesk(recorder)
        desk.replaceAll([report("2025-12-31")])

        XCTAssertEqual(desk.add(), .failed("Boom()"))
        recorder.failRemove = true
        XCTAssertEqual(desk.delete(periodEnd: "2025-12-31"), .failed("Boom()"))
    }

    func testReplaceAllCancelsPendingPersist() async {
        let recorder = Recorder()
        let desk = makeDesk(recorder)
        desk.replaceAll([report("2025-12-31")])
        desk.update(periodEnd: "2025-12-31", field: .marketValue, value: 9)
        desk.replaceAll([report("2026-03-31")])
        await Task.yield()
        await Task.yield()
        XCTAssertTrue(recorder.upserts.isEmpty)
        XCTAssertEqual(desk.reports.map(\.periodEnd), ["2026-03-31"])
    }

    // MARK: - helpers

    private func makeDesk(_ recorder: Recorder) -> QuarterlyDesk {
        QuarterlyDesk(persistence: .init(
            upsert: { reports in
                if recorder.failUpsert { throw Boom() }
                recorder.upserts.append(reports)
            },
            remove: { end in
                if recorder.failRemove { throw Boom() }
                recorder.removed.append(end)
            },
            rename: { from, to in
                if recorder.failRename { throw Boom() }
                recorder.renames.append((from, to))
            }), debounce: .zero, sleep: { _ in })
    }

    private func report(_ end: String) -> QuarterlyReport {
        QuarterlyReport(periodEnd: end, marketValue: 1, source: "manual")
    }
}
