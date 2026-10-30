/**
 * 拖放落点判定：坐标 → 字段，路径 → 引用目录还是加入文件。
 *
 * 这块逻辑一旦写错，用户就会"把素材拖到输出目录里，结果素材被当成输入"，
 * 所以每个分支都单独钉住。
 */
import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import {
  DIRECTORY_ZONES,
  findDropZone,
  folderToReference,
  toCssPoint,
  type DropZoneRect,
} from '../src/utils/dropZones.ts'

const zones: DropZoneRect[] = [
  { id: 'input', left: 0, top: 0, right: 100, bottom: 100 },
  { id: 'outputDir', left: 0, top: 200, right: 100, bottom: 260 },
]

describe('findDropZone', () => {
  it('命中输入区', () => {
    assert.equal(findDropZone({ x: 50, y: 50 }, zones), 'input')
  })

  it('命中输出目录', () => {
    assert.equal(findDropZone({ x: 50, y: 230 }, zones), 'outputDir')
  })

  it('空白处不命中', () => {
    assert.equal(findDropZone({ x: 500, y: 500 }, zones), null)
  })

  it('没有坐标时返回 null（原生事件可能不带位置）', () => {
    assert.equal(findDropZone(null, zones), null)
    assert.equal(findDropZone({ x: Number.NaN, y: 0 }, zones), null)
  })

  it('边界值算命中（贴着上沿也算）', () => {
    assert.equal(findDropZone({ x: 0, y: 200 }, zones), 'outputDir')
  })
})

describe('toCssPoint', () => {
  it('按 devicePixelRatio 换算', () => {
    assert.deepEqual(toCssPoint({ x: 200, y: 400 }, 2), { x: 100, y: 200 })
  })

  it('比例非法时按 1 处理', () => {
    assert.deepEqual(toCssPoint({ x: 10, y: 20 }, 0), { x: 10, y: 20 })
    assert.deepEqual(toCssPoint({ x: 10, y: 20 }, Number.NaN), { x: 10, y: 20 })
  })

  it('没有坐标返回 null', () => {
    assert.equal(toCssPoint(null, 2), null)
  })
})

describe('folderToReference', () => {
  it('单个目录 → 引用它', () => {
    assert.equal(folderToReference([{ path: 'C:/out', kind: 'directory' }]), 'C:/out')
  })

  it('单个文件 → 不引用（交给"加入文件"）', () => {
    assert.equal(folderToReference([{ path: 'C:/a.png', kind: 'file' }]), null)
  })

  it('多个路径 → 不引用（避免把素材目录误设成输出目录）', () => {
    assert.equal(
      folderToReference([
        { path: 'C:/a', kind: 'directory' },
        { path: 'C:/b', kind: 'directory' },
      ]),
      null,
    )
  })

  it('不存在的路径 → 不引用', () => {
    assert.equal(folderToReference([{ path: 'C:/nope', kind: 'missing' }]), null)
  })

  it('空输入 → null', () => {
    assert.equal(folderToReference(null), null)
    assert.equal(folderToReference([]), null)
  })
})

describe('DIRECTORY_ZONES', () => {
  it('只有目录型字段会引用目录', () => {
    assert.ok(DIRECTORY_ZONES.has('outputDir'))
    assert.ok(DIRECTORY_ZONES.has('modelDir'))
    assert.ok(!DIRECTORY_ZONES.has('input'))
  })
})
