package main

import (
	"fmt"
	"math/bits"
	"strings"
)

type termSearchRoute struct{}
type postingsMergeRoute struct{}
type searchRankRoute struct{}
type snippetRoute struct{}

type parseRoute struct{}
type resolveRoute struct{}
type flowRoute struct{}
type workspaceIndexRoute struct{}

type tableScanRoute struct{}
type indexProbeRoute struct{}
type joinRoute struct{}
type aggregateRoute struct{}

type bytecodeDispatchRoute struct{}
type objectModelRoute struct{}
type exceptionRoute struct{}
type moduleImportRoute struct{}

type layoutTreeRoute struct{}
type fontMetricRoute struct{}
type areaResolutionRoute struct{}
type renderPageRoute struct{}

func runBenchmarkInvocation(requests int, seed uint64, benchmark string) (uint64, string, error) {
	normalized, ok := normalizeBenchmark(benchmark)
	if !ok {
		return 0, "", fmt.Errorf("unknown benchmark %q; valid benchmarks: router, dacapo-lusearch, dacapo-eclipse, dacapo-h2, dacapo-jython, dacapo-fop", benchmark)
	}

	switch normalized {
	case "router":
		return runRouterInvocation(requests, seed), normalized, nil
	case "dacapo-lusearch":
		return runLusearchInvocation(requests, seed), normalized, nil
	case "dacapo-eclipse":
		return runEclipseInvocation(requests, seed), normalized, nil
	case "dacapo-h2":
		return runH2Invocation(requests, seed), normalized, nil
	case "dacapo-jython":
		return runJythonInvocation(requests, seed), normalized, nil
	case "dacapo-fop":
		return runFopInvocation(requests, seed), normalized, nil
	default:
		return 0, "", fmt.Errorf("unhandled benchmark %q", normalized)
	}
}

func normalizeBenchmark(name string) (string, bool) {
	switch strings.ToLower(strings.TrimSpace(name)) {
	case "", "router", "go-router", "http-router":
		return "router", true
	case "lusearch", "dacapo-lusearch", "dacapo:lusearch":
		return "dacapo-lusearch", true
	case "eclipse", "dacapo-eclipse", "dacapo:eclipse":
		return "dacapo-eclipse", true
	case "h2", "dacapo-h2", "dacapo:h2":
		return "dacapo-h2", true
	case "jython", "dacapo-jython", "dacapo:jython":
		return "dacapo-jython", true
	case "fop", "dacapo-fop", "dacapo:fop":
		return "dacapo-fop", true
	default:
		return "", false
	}
}

func runLusearchInvocation(requests int, seed uint64) uint64 {
	ops := []operation{
		termSearchRoute{},
		termSearchRoute{},
		termSearchRoute{},
		postingsMergeRoute{},
		postingsMergeRoute{},
		searchRankRoute{},
		snippetRoute{},
	}
	return runOperationMix(requests, seed, 0x6c75736561726368, ops, func(selector uint64) int {
		switch {
		case selector < 70:
			return int(selector) % 3
		case selector < 90:
			return 3 + int(selector&1)
		case selector < 98:
			return 5
		default:
			return 6
		}
	}, 10)
}

func runEclipseInvocation(requests int, seed uint64) uint64 {
	ops := []operation{
		parseRoute{},
		parseRoute{},
		resolveRoute{},
		resolveRoute{},
		resolveRoute{},
		flowRoute{},
		workspaceIndexRoute{},
	}
	return runOperationMix(requests, seed, 0x65636c6970736521, ops, func(selector uint64) int {
		switch {
		case selector < 35:
			return int(selector) & 1
		case selector < 80:
			return 2 + int(selector%3)
		case selector < 96:
			return 5
		default:
			return 6
		}
	}, 11)
}

func runH2Invocation(requests int, seed uint64) uint64 {
	ops := []operation{
		tableScanRoute{},
		tableScanRoute{},
		tableScanRoute{},
		indexProbeRoute{},
		indexProbeRoute{},
		joinRoute{},
		aggregateRoute{},
	}
	return runOperationMix(requests, seed, 0x68325f7175657279, ops, func(selector uint64) int {
		switch {
		case selector < 55:
			return int(selector % 3)
		case selector < 82:
			return 3 + int(selector&1)
		case selector < 94:
			return 5
		default:
			return 6
		}
	}, 12)
}

func runJythonInvocation(requests int, seed uint64) uint64 {
	ops := []operation{
		bytecodeDispatchRoute{},
		bytecodeDispatchRoute{},
		bytecodeDispatchRoute{},
		objectModelRoute{},
		objectModelRoute{},
		exceptionRoute{},
		moduleImportRoute{},
	}
	return runOperationMix(requests, seed, 0x6a7974686f6e213f, ops, func(selector uint64) int {
		switch {
		case selector < 62:
			return int(selector % 3)
		case selector < 86:
			return 3 + int(selector&1)
		case selector < 96:
			return 5
		default:
			return 6
		}
	}, 13)
}

func runFopInvocation(requests int, seed uint64) uint64 {
	ops := []operation{
		layoutTreeRoute{},
		layoutTreeRoute{},
		layoutTreeRoute{},
		fontMetricRoute{},
		fontMetricRoute{},
		areaResolutionRoute{},
		renderPageRoute{},
	}
	return runOperationMix(requests, seed, 0x666f705f6c61796f, ops, func(selector uint64) int {
		switch {
		case selector < 58:
			return int(selector % 3)
		case selector < 78:
			return 3 + int(selector&1)
		case selector < 92:
			return 5
		default:
			return 6
		}
	}, 14)
}

func runOperationMix(requests int, seed uint64, salt uint64, ops []operation, choose func(uint64) int, rounds int) uint64 {
	var total uint64 = seed ^ salt ^ 0x6a09e667f3bcc909
	state := seed + salt + 0x9e3779b97f4a7c15
	for i := 0; i < requests; i++ {
		state = nextState(state + uint64(i)*0x100000001b3)
		ev := makeEvent(state^salt, uint64(i))
		op := ops[choose(state%100)]
		total ^= handleBenchmarkBurst(op, ev, rounds) + uint64(i)*(salt|1)
		total = bits.RotateLeft64(total, int((ev.Weight^salt)&31))
	}
	return total
}

func handleBenchmarkBurst(op operation, ev event, rounds int) uint64 {
	var total uint64
	for i := 0; i < rounds; i++ {
		ev.Payload[i&7] ^= uint64(i)*0x9e3779b97f4a7c15 + total
		total ^= op.Apply(ev)
		total = bits.RotateLeft64(total, int(ev.Route)+i+1)
	}
	return total
}

func (termSearchRoute) Apply(ev event) uint64 {
	query := foldHash(ev.Tenant ^ ev.Route ^ ev.Weight)
	var hits uint64
	for i, word := range ev.Payload {
		term := foldHash(word ^ uint64(i)*0x517cc1b727220a95)
		if term&0xfff == query&0xfff {
			hits += uint64(bits.OnesCount64(term^query) + 13)
		} else {
			hits ^= bits.RotateLeft64(term+query, i+3)
		}
	}
	return finalize(hits ^ query)
}

func (postingsMergeRoute) Apply(ev event) uint64 {
	cursor := ev.Weight | 1
	score := ev.Tenant + 17
	for pass := 0; pass < 4; pass++ {
		for _, word := range ev.Payload {
			gap := (word >> uint(pass*7)) & 0x7ff
			cursor += gap + uint64(pass)
			if cursor&3 == ev.Route&3 {
				score ^= foldHash(cursor ^ word)
			} else {
				score += bits.Reverse64(cursor) ^ word
			}
		}
		score = bits.RotateLeft64(score, 9)
	}
	return finalize(score)
}

func (searchRankRoute) Apply(ev event) uint64 {
	score := ev.Weight ^ 0x9ddfea08eb382d69
	for i, word := range ev.Payload {
		tf := uint64(bits.OnesCount64(word^ev.Tenant) + 1)
		idf := ((word >> uint(i+3)) & 0xff) + 17
		score += tf * idf * uint64(i+1)
		score ^= bits.RotateLeft64(score, int(ev.Route)+i)
	}
	return finalize(score)
}

func (snippetRoute) Apply(ev event) uint64 {
	window := ev.Tenant ^ 0x243f6a8885a308d3
	for i := 0; i < 5; i++ {
		for _, word := range ev.Payload {
			mask := uint64(0xff) << uint((i*11)&56)
			window ^= foldHash((word & mask) + ev.Weight + uint64(i))
			window = bits.RotateLeft64(window, 7)
		}
	}
	return finalize(window)
}

func (parseRoute) Apply(ev event) uint64 {
	stack := ev.Route + 1
	for i, word := range ev.Payload {
		tokenClass := (word >> uint((i*5)&63)) & 15
		if tokenClass < 9 {
			stack += tokenClass + uint64(i)
		} else {
			stack ^= bits.RotateLeft64(word, int(tokenClass))
		}
		stack = foldHash(stack ^ ev.Weight)
	}
	return finalize(stack)
}

func (resolveRoute) Apply(ev event) uint64 {
	symbol := ev.Tenant ^ 0x100000001b3
	for pass := 0; pass < 3; pass++ {
		for i, word := range ev.Payload {
			bucket := foldHash(word+uint64(pass)*ev.Weight) & 0x3ff
			if bucket == (ev.Route+uint64(i))&0x3ff {
				symbol ^= bucket + word
			} else {
				symbol += bits.RotateLeft64(bucket^word, pass+i)
			}
		}
	}
	return finalize(symbol)
}

func (flowRoute) Apply(ev event) uint64 {
	state := ev.Weight ^ 0xbf58476d1ce4e5b9
	for i := 0; i < 6; i++ {
		left := ev.Payload[i&7]
		right := ev.Payload[(i+3)&7]
		edge := foldHash(left ^ bits.RotateLeft64(right, i+1))
		state ^= edge + uint64(bits.OnesCount64(state&edge))
		state = bits.RotateLeft64(state, 13)
	}
	return finalize(state)
}

func (workspaceIndexRoute) Apply(ev event) uint64 {
	index := ev.Tenant + ev.Route
	for shard := 0; shard < 4; shard++ {
		for _, word := range ev.Payload {
			index ^= foldHash(word + uint64(shard)*0x94d049bb133111eb)
			index += uint64(bits.LeadingZeros64(index|1)) + ev.Weight
		}
	}
	return finalize(index)
}

func (tableScanRoute) Apply(ev event) uint64 {
	rows := ev.Weight ^ 0xc4ceb9fe1a85ec53
	for i, word := range ev.Payload {
		predicate := ((word >> uint(i*3)) ^ ev.Tenant) & 0xff
		if predicate < 96 {
			rows += foldHash(word ^ predicate)
		} else {
			rows ^= bits.Reverse64(word + predicate)
		}
	}
	return finalize(rows)
}

func (indexProbeRoute) Apply(ev event) uint64 {
	probe := ev.Tenant ^ ev.Route
	for level := 0; level < 5; level++ {
		word := ev.Payload[level&7]
		probe = foldHash(probe + word + uint64(level))
		if probe&7 == 0 {
			probe ^= bits.RotateLeft64(word, level+3)
		}
	}
	return finalize(probe)
}

func (joinRoute) Apply(ev event) uint64 {
	hash := ev.Weight + 0xff51afd7ed558ccd
	for i := 0; i < 8; i++ {
		left := foldHash(ev.Payload[i&7] ^ ev.Tenant)
		right := foldHash(ev.Payload[(i+5)&7] ^ ev.Route)
		if left&0x1f == right&0x1f {
			hash += left ^ right
		} else {
			hash ^= bits.RotateLeft64(left+right, i+1)
		}
	}
	return finalize(hash)
}

func (aggregateRoute) Apply(ev event) uint64 {
	acc := ev.Route + 0x13198a2e03707344
	for group := 0; group < 4; group++ {
		sum := uint64(group) + ev.Tenant
		for _, word := range ev.Payload {
			sum += (word >> uint(group*9)) & 0xffff
			sum ^= foldHash(sum + ev.Weight)
		}
		acc ^= bits.RotateLeft64(sum, group*7+1)
	}
	return finalize(acc)
}

func (bytecodeDispatchRoute) Apply(ev event) uint64 {
	frame := ev.Weight ^ 0x4f1bbcdc6765f347
	for pc := 0; pc < 16; pc++ {
		opcode := (ev.Payload[pc&7] >> uint((pc*5)&63)) & 0x3f
		if opcode < 38 {
			frame += foldHash(opcode + ev.Tenant + uint64(pc))
		} else {
			frame ^= bits.RotateLeft64(ev.Payload[(pc+3)&7]^opcode, pc+1)
		}
		frame = bits.RotateLeft64(frame, int(opcode&15)+1)
	}
	return finalize(frame)
}

func (objectModelRoute) Apply(ev event) uint64 {
	shape := ev.Tenant ^ ev.Route ^ 0x9e3779b97f4a7c15
	for pass := 0; pass < 5; pass++ {
		for i, word := range ev.Payload {
			slot := foldHash(word+uint64(pass)*0x100000001b3) & 0xff
			if slot < 160 {
				shape += slot * uint64(i+1)
			} else {
				shape ^= bits.Reverse64(slot + word + ev.Weight)
			}
		}
		shape = foldHash(shape)
	}
	return finalize(shape)
}

func (exceptionRoute) Apply(ev event) uint64 {
	stack := ev.Weight + 0x243f6a8885a308d3
	for depth := 0; depth < 9; depth++ {
		word := ev.Payload[depth&7]
		mask := foldHash(word ^ uint64(depth)*ev.Tenant)
		if mask&0xf == ev.Route&0xf {
			stack ^= mask + uint64(bits.TrailingZeros64(mask|1))
		} else {
			stack += bits.RotateLeft64(mask^word, depth+5)
		}
	}
	return finalize(stack)
}

func (moduleImportRoute) Apply(ev event) uint64 {
	cache := ev.Tenant + 0xc4ceb9fe1a85ec53
	for pass := 0; pass < 4; pass++ {
		for _, word := range ev.Payload {
			nameHash := foldHash(word ^ cache ^ uint64(pass))
			cache ^= nameHash + uint64(bits.OnesCount64(nameHash))
			cache = bits.RotateLeft64(cache, 11)
		}
	}
	return finalize(cache)
}

func (layoutTreeRoute) Apply(ev event) uint64 {
	layout := ev.Weight ^ 0x6a09e667f3bcc909
	for node := 0; node < 12; node++ {
		word := ev.Payload[node&7]
		width := (word >> uint((node*3)&63)) & 0x3ff
		breakClass := (word >> uint((node*7)&63)) & 0x1f
		if breakClass < 21 {
			layout += foldHash(width + ev.Tenant + uint64(node))
		} else {
			layout ^= bits.RotateLeft64(width^word, node+2)
		}
	}
	return finalize(layout)
}

func (fontMetricRoute) Apply(ev event) uint64 {
	metrics := ev.Route + 0xbf58476d1ce4e5b9
	for i, word := range ev.Payload {
		glyph := (word >> uint(i*6)) & 0xffff
		advance := foldHash(glyph+ev.Weight+uint64(i)) & 0x7ff
		metrics += advance * uint64(bits.OnesCount64(glyph|1))
		metrics ^= bits.RotateLeft64(metrics, i+3)
	}
	return finalize(metrics)
}

func (areaResolutionRoute) Apply(ev event) uint64 {
	area := ev.Tenant ^ 0xff51afd7ed558ccd
	for pass := 0; pass < 6; pass++ {
		left := foldHash(ev.Payload[pass&7] + uint64(pass))
		right := foldHash(ev.Payload[(pass+2)&7] ^ ev.Weight)
		if left&0x3f == right&0x3f {
			area += left ^ right
		} else {
			area ^= bits.RotateLeft64(left+right, pass+7)
		}
	}
	return finalize(area)
}

func (renderPageRoute) Apply(ev event) uint64 {
	paint := ev.Weight + ev.Route + 0x94d049bb133111eb
	for layer := 0; layer < 5; layer++ {
		for _, word := range ev.Payload {
			paint ^= foldHash(word + uint64(layer)*paint)
			paint += uint64(bits.LeadingZeros64(paint|1)) + ev.Tenant
		}
	}
	return finalize(paint)
}
