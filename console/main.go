// dlna-console —— DLNA 音频桥的应用内控制台
//
// 职责：枚举硬件、读写 renderer.conf、重启渲染器让它生效、放测试音。
// 不做的事：不做启停开关（交给应用中心的启用/停用）、不碰 gmediarender 的启动参数
//          （那一律由 cmd/main 负责，见 restart-renderer 子命令）。
//
// 以 root 运行（要执行 aplay / ip、写配置、重启进程），因此只监听内网 IPv4，
// 端口 8790 由 manifest 的 service_port 暴露给应用中心。
package main

import (
	"bytes"
	"context"
	_ "embed"
	"encoding/binary"
	"encoding/json"
	"fmt"
	"html"
	"io"
	"log"
	"math"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"syscall"
	"time"
)

const (
	upnpPort    = 49494
	defaultPort = "8790"
	nameMaxRune = 32

	// 控制地址不是标准的 AVTransport1 —— gmediarender 用的是 rendertransport1，
	// 连错会 404（这坑踩过，别改）。
	ctrlTransport = "/upnp/control/rendertransport1"
	ctrlRendering = "/upnp/control/rendercontrol1"
	nsTransport   = "urn:schemas-upnp-org:service:AVTransport:1"
	nsRendering   = "urn:schemas-upnp-org:service:RenderingControl:1"
)

//go:embed index.html
var indexHTML []byte

// ---------------------------------------------------------------- 路径

type paths struct {
	varDir   string
	conf     string
	pidFile  string
	renderer string // renderer.log
	cmdMain  string
	manifest string
}

func resolvePaths() paths {
	varDir := os.Getenv("TRIM_PKGVAR")
	if varDir == "" {
		varDir = "/vol1/@appdata/dlna-renderer"
	}
	p := paths{
		varDir:   varDir,
		conf:     filepath.Join(varDir, "renderer.conf"),
		pidFile:  filepath.Join(varDir, "pid"),
		renderer: filepath.Join(varDir, "renderer.log"),
		cmdMain:  os.Getenv("DLNA_CMD_MAIN"),
		manifest: os.Getenv("DLNA_MANIFEST"),
	}
	if p.cmdMain == "" {
		p.cmdMain = "/var/apps/dlna-renderer/cmd/main"
	}
	if p.manifest == "" {
		p.manifest = "/var/apps/dlna-renderer/manifest"
	}
	return p
}

// ---------------------------------------------------------------- 配置读写

// conf 是 shell 可 source 的 KEY="value" 格式，读写都要保留原有的注释与顺序，
// 否则用户手写的注释会被抹掉。
func readConf(path string) map[string]string {
	vals := map[string]string{}
	data, err := os.ReadFile(path)
	if err != nil {
		return vals
	}
	for _, line := range strings.Split(string(data), "\n") {
		t := strings.TrimSpace(line)
		if t == "" || strings.HasPrefix(t, "#") {
			continue
		}
		k, v, ok := strings.Cut(t, "=")
		if !ok {
			continue
		}
		vals[strings.TrimSpace(k)] = unquote(strings.TrimSpace(v))
	}
	return vals
}

func unquote(s string) string {
	if len(s) < 2 {
		return s
	}
	q := s[0]
	if !((q == '"' || q == '\'') && s[len(s)-1] == q) {
		return s
	}
	body := s[1 : len(s)-1]
	if q == '\'' {
		return strings.ReplaceAll(body, `'\''`, `'`)
	}
	var b strings.Builder
	for i := 0; i < len(body); i++ {
		if body[i] == '\\' && i+1 < len(body) {
			i++
			switch body[i] {
			case 'n':
				b.WriteByte('\n')
			case 't':
				b.WriteByte('\t')
			default:
				b.WriteByte(body[i])
			}
			continue
		}
		b.WriteByte(body[i])
	}
	return b.String()
}

// 双引号包裹并转义 —— 配置最后是被 shell `.` 进来的，
// 名字里若出现 $ 或反引号会被展开，必须挡住。
func shellQuote(s string) string {
	var b strings.Builder
	b.WriteByte('"')
	for _, r := range s {
		switch r {
		case '"':
			b.WriteString(`\"`)
		case '\\':
			b.WriteString(`\\`)
		case '$':
			b.WriteString(`\$`)
		case '`':
			b.WriteString("\\`")
		default:
			b.WriteRune(r)
		}
	}
	b.WriteByte('"')
	return b.String()
}

var confOrder = []string{"UDN", "FRIENDLY_NAME", "IFACE", "AUDIODEV"}

func writeConf(path string, updates map[string]string) error {
	var lines []string
	if data, err := os.ReadFile(path); err == nil {
		lines = strings.Split(strings.TrimRight(string(data), "\n"), "\n")
	}

	pending := map[string]string{}
	for k, v := range updates {
		pending[k] = v
	}
	for i, line := range lines {
		t := strings.TrimSpace(line)
		if t == "" || strings.HasPrefix(t, "#") {
			continue
		}
		k, _, ok := strings.Cut(t, "=")
		if !ok {
			continue
		}
		key := strings.TrimSpace(k)
		if v, want := pending[key]; want {
			lines[i] = key + "=" + shellQuote(v)
			delete(pending, key)
		}
	}
	for _, key := range confOrder {
		if v, ok := pending[key]; ok {
			lines = append(lines, key+"="+shellQuote(v))
			delete(pending, key)
		}
	}
	for k, v := range pending {
		lines = append(lines, k+"="+shellQuote(v))
	}

	out := strings.Join(lines, "\n") + "\n"
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, []byte(out), 0o644); err != nil {
		return err
	}
	return os.Rename(tmp, path)
}

// ---------------------------------------------------------------- 硬件枚举

type soundCard struct {
	Value string `json:"value"` // plughw:CARD=PCH,DEV=0
	Card  string `json:"card"`  // PCH
	Index int    `json:"index"` // 0
	Dev   int    `json:"dev"`   // 0
	Name  string `json:"name"`  // ALC887-VD Analog
	Chip  string `json:"chip"`  // HDA Intel PCH
	Busy  bool   `json:"busy"`
}

var cardRe = regexp.MustCompile(`^card (\d+): (\S+) \[([^\]]*)\], device (\d+): (.+?) \[(.*?)\]`)

// PC 蜂鸣器（pcsp）也能被 ALSA 当成播放设备，但它只会「嘀」一声，
// 选进列表里纯属噪音，直接过滤。
func isVirtualCard(name, chip string) bool {
	if strings.EqualFold(name, "pcsp") {
		return true
	}
	c := strings.ToLower(chip)
	return strings.Contains(c, "pc-speaker") || strings.Contains(c, "pc speaker")
}

func listCards() []soundCard {
	out, err := exec.Command("aplay", "-l").Output()
	if err != nil {
		return nil
	}
	var cards []soundCard
	// plug:dmix 是 ALSA 的软件混音层，不在 aplay -l 里，但它才是渲染器该用的设备：
	// 手机调音量时的提示音要和音乐同时出声，只有 dmix 允许多个客户端同时打开。
	// 放列表最前面，默认就是它。
	if hasDmix() {
		cards = append(cards, soundCard{
			Value: "plug:dmix",
			Card:  "dmix",
			Index: -1,
			Dev:   -1,
			Name:  "共享输出（推荐，支持提示音）",
			Chip:  "ALSA dmix",
			Busy:  false,
		})
	}
	for _, line := range strings.Split(string(out), "\n") {
		m := cardRe.FindStringSubmatch(strings.TrimSpace(line))
		if m == nil {
			continue
		}
		if isVirtualCard(m[2], m[3]) {
			continue
		}
		idx, _ := strconv.Atoi(m[1])
		dev, _ := strconv.Atoi(m[4])
		cards = append(cards, soundCard{
			Value: fmt.Sprintf("plughw:CARD=%s,DEV=%d", m[2], dev),
			Card:  m[2],
			Index: idx,
			Dev:   dev,
			Name:  strings.TrimSpace(m[5]),
			Chip:  strings.TrimSpace(m[3]),
			Busy:  pcmBusy(idx, dev),
		})
	}
	return cards
}

// dmix 可用吗。注意 aplay -L 里 dmix 只以 "dmix" 或 "dmix:CARD=..." 出现，
// 而渲染器用的是我们自己拼的 "plug:dmix"（让 plug 帮做格式/采样率转换）——
// 拿 "plug:dmix" 去比对永远不匹配，1.1.7 就是在这里判断失灵，
// 结果设备没切过去、提示音被独占的 plughw 挡住。
func hasDmix() bool {
	out, err := exec.Command("aplay", "-L").Output()
	if err != nil {
		return false
	}
	for _, line := range strings.Split(string(out), "\n") {
		s := strings.TrimSpace(line)
		if s == "dmix" || strings.HasPrefix(s, "dmix:") ||
			s == "plug:dmix" || strings.HasPrefix(s, "plug:dmix:") {
			return true
		}
	}
	return false
}

// 声卡被占用时切换过去会静默失败（UPnP 报 PLAYING 但没声音），
// 界面上要能提前看到这个状态。
func pcmBusy(cardIdx, dev int) bool {
	p := fmt.Sprintf("/proc/asound/card%d/pcm%dp/sub0/status", cardIdx, dev)
	data, err := os.ReadFile(p)
	if err != nil {
		return false
	}
	return strings.Contains(string(data), "RUNNING")
}

type ifaceInfo struct {
	Name      string `json:"name"`
	IPv4      string `json:"ipv4"`
	Default   bool   `json:"default"`
	Recommended bool `json:"recommended"`
}

var skipIfacePrefix = []string{
	"lo", "docker", "veth", "br-", "virbr", "tailscale", "tun", "tap", "cni", "flannel",
}

func skipIface(name string) bool {
	for _, p := range skipIfacePrefix {
		if strings.HasPrefix(name, p) {
			return true
		}
	}
	return false
}

var addrRe = regexp.MustCompile(`^\d+:\s+(\S+)\s+inet\s+(\d+\.\d+\.\d+\.\d+)/`)

func listIfaces() []ifaceInfo {
	defaultIface := defaultRouteIface()
	out, err := exec.Command("ip", "-4", "-o", "addr", "show").Output()
	if err != nil {
		return nil
	}
	var list []ifaceInfo
	seen := map[string]bool{}
	for _, line := range strings.Split(string(out), "\n") {
		m := addrRe.FindStringSubmatch(strings.TrimSpace(line))
		if m == nil || seen[m[1]] || skipIface(m[1]) {
			continue
		}
		seen[m[1]] = true
		list = append(list, ifaceInfo{
			Name:    m[1],
			IPv4:    m[2],
			Default: m[1] == defaultIface,
			// SSDP 靠组播，选错网卡手机就完全搜不到设备，所以默认路由那张最稳
			Recommended: m[1] == defaultIface,
		})
	}
	return list
}

func defaultRouteIface() string {
	out, err := exec.Command("ip", "-4", "route", "show", "default").Output()
	if err != nil {
		return ""
	}
	fields := strings.Fields(string(out))
	for i := 0; i+1 < len(fields); i++ {
		if fields[i] == "dev" {
			return fields[i+1]
		}
	}
	return ""
}

func ifaceIPv4(name string) string {
	ifaces, err := net.InterfaceByName(name)
	if err != nil {
		return ""
	}
	addrs, err := ifaces.Addrs()
	if err != nil {
		return ""
	}
	for _, a := range addrs {
		if ipnet, ok := a.(*net.IPNet); ok {
			if v4 := ipnet.IP.To4(); v4 != nil {
				return v4.String()
			}
		}
	}
	return ""
}

// ---------------------------------------------------------------- 渲染器控制

func rendererPID(pidFile string) int {
	data, err := os.ReadFile(pidFile)
	if err != nil {
		return 0
	}
	pid, err := strconv.Atoi(strings.TrimSpace(string(data)))
	if err != nil || pid <= 0 {
		return 0
	}
	return pid
}

func pidAlive(pid int) bool {
	if pid <= 0 {
		return false
	}
	// 用 Signal(0) 而不是 syscall.Kill：等价于探测进程是否存在，
	// 但不真正发信号，且不绑定到某个平台的 syscall 包。
	proc, err := os.FindProcess(pid)
	if err != nil {
		return false
	}
	return proc.Signal(syscall.Signal(0)) == nil
}

func rendererRunning(p paths) bool {
	return pidAlive(rendererPID(p.pidFile))
}

func restartRenderer(p paths) (string, error) {
	ctx, cancel := context.WithTimeout(context.Background(), 25*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, p.cmdMain, "restart-renderer")
	out, err := cmd.CombinedOutput()
	return string(out), err
}

// 改名之后 libupnp 不会热更新设备描述，必须重启；
// 重启完还得真去把 description.xml 拉下来，确认新名字已经广播出去了，
// 只回个 200 就完事的话，用户会以为改成功了其实没有。
func probeFriendlyName(host string) (string, error) {
	if host == "" {
		host = "127.0.0.1"
	}
	client := &http.Client{Timeout: 3 * time.Second}
	resp, err := client.Get(fmt.Sprintf("http://%s:%d/description.xml", host, upnpPort))
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(io.LimitReader(resp.Body, 64<<10))
	if err != nil {
		return "", err
	}
	m := regexp.MustCompile(`<friendlyName>(.*?)</friendlyName>`).FindSubmatch(body)
	if m == nil {
		return "", fmt.Errorf("description.xml 里找不到 friendlyName")
	}
	return strings.TrimSpace(string(m[1])), nil
}

func tailFile(path string, lines int) string {
	f, err := os.Open(path)
	if err != nil {
		return ""
	}
	defer f.Close()
	st, err := f.Stat()
	if err != nil {
		return ""
	}
	const chunk = 16 << 10
	start := st.Size() - chunk
	if start < 0 {
		start = 0
	}
	if _, err := f.Seek(start, io.SeekStart); err != nil {
		return ""
	}
	data, err := io.ReadAll(f)
	if err != nil {
		return ""
	}
	parts := strings.Split(strings.TrimRight(string(data), "\n"), "\n")
	if len(parts) > lines {
		parts = parts[len(parts)-lines:]
	}
	return strings.Join(parts, "\n")
}

// 生成一段正弦波 WAV。用 aplay 直接放，不依赖 GStreamer ——
// 声卡选错时这是唯一能立刻判断"到底有没有声音"的手段。
func makeToneWAV(freq, seconds float64) []byte {
	const rate = 44100
	n := int(float64(rate) * seconds)
	dataSize := n * 2 // 16bit mono

	var buf bytes.Buffer
	buf.WriteString("RIFF")
	binary.Write(&buf, binary.LittleEndian, uint32(36+dataSize))
	buf.WriteString("WAVEfmt ")
	binary.Write(&buf, binary.LittleEndian, uint32(16))    // PCM 头长度
	binary.Write(&buf, binary.LittleEndian, uint16(1))     // format = PCM
	binary.Write(&buf, binary.LittleEndian, uint16(1))     // channels
	binary.Write(&buf, binary.LittleEndian, uint32(rate))  // sample rate
	binary.Write(&buf, binary.LittleEndian, uint32(rate*2)) // byte rate
	binary.Write(&buf, binary.LittleEndian, uint16(2))     // block align
	binary.Write(&buf, binary.LittleEndian, uint16(16))    // bits
	buf.WriteString("data")
	binary.Write(&buf, binary.LittleEndian, uint32(dataSize))

	step := 2 * math.Pi * freq / rate
	for i := 0; i < n; i++ {
		// 首尾各 50ms 做淡入淡出，避免爆音
		amp := 0.28
		pos := float64(i) / float64(n)
		if pos < 0.05 {
			amp *= pos / 0.05
		} else if pos > 0.95 {
			amp *= (1 - pos) / 0.05
		}
		v := int16(32767 * amp * math.Sin(step*float64(i)))
		binary.Write(&buf, binary.LittleEndian, v)
	}
	return buf.Bytes()
}

func playTone(dev string) error {
	wav := makeToneWAV(440, 2.0)
	tmp := filepath.Join(os.TempDir(), "dlna-console-tone.wav")
	if err := os.WriteFile(tmp, wav, 0o644); err != nil {
		return fmt.Errorf("写测试音文件失败: %w", err)
	}
	defer os.Remove(tmp)

	ctx, cancel := context.WithTimeout(context.Background(), 12*time.Second)
	defer cancel()
	out, err := exec.CommandContext(ctx, "aplay", "-q", "-D", dev, tmp).CombinedOutput()
	if err != nil {
		msg := strings.TrimSpace(string(out))
		if msg == "" {
			msg = err.Error()
		}
		return fmt.Errorf("%s", msg)
	}
	return nil
}

// ---------------------------------------------------------------- UPnP 播放状态 / 控制

// 控制台自己不去拉流，只通过 SOAP 指挥渲染器 —— 播放链路的唯一真相仍在 gmediarender。
var upnpClient = &http.Client{Timeout: 4 * time.Second}

func soapCall(host, path, ns, action, args string) (string, error) {
	if host == "" {
		return "", fmt.Errorf("没有可用的网卡地址")
	}
	body := fmt.Sprintf(
		`<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" `+
			`s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>`+
			`<u:%s xmlns:u="%s"><InstanceID>0</InstanceID>%s</u:%s></s:Body></s:Envelope>`,
		action, ns, args, action)
	req, err := http.NewRequest(http.MethodPost,
		fmt.Sprintf("http://%s:%d%s", host, upnpPort, path), strings.NewReader(body))
	if err != nil {
		return "", err
	}
	req.Header.Set("Content-Type", `text/xml; charset="utf-8"`)
	req.Header.Set("SOAPACTION", `"`+ns+"#"+action+`"`)
	resp, err := upnpClient.Do(req)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()
	data, err := io.ReadAll(io.LimitReader(resp.Body, 128<<10))
	if err != nil {
		return "", err
	}
	if resp.StatusCode != http.StatusOK {
		return string(data), fmt.Errorf("渲染器返回 %d", resp.StatusCode)
	}
	return string(data), nil
}

// SOAP 响应里的元数据是转义过的 XML（&lt;dc:title&gt;），先反转义再取值，
// 所以这个函数会被套两层用。
func tagValue(s, tag string) string {
	re := regexp.MustCompile(`(?s)<` + regexp.QuoteMeta(tag) + `>(.*?)</` + regexp.QuoteMeta(tag) + `>`)
	m := re.FindStringSubmatch(s)
	if m == nil {
		return ""
	}
	return strings.TrimSpace(html.UnescapeString(m[1]))
}

func hmsToSec(v string) int {
	v = strings.TrimSpace(v)
	if v == "" || !strings.Contains(v, ":") {
		return 0
	}
	total := 0
	for _, part := range strings.Split(v, ":") {
		part = strings.SplitN(part, ".", 2)[0] // 有的实现会带毫秒
		n, err := strconv.Atoi(part)
		if err != nil || n < 0 {
			return 0
		}
		total = total*60 + n
	}
	return total
}

func secToHMS(sec int) string {
	return fmt.Sprintf("%d:%02d:%02d", sec/3600, (sec%3600)/60, sec%60)
}

// 渲染器只监听配置里那张网卡的地址，所以查状态也得走同一个地址。
func rendererHost(p paths) string {
	c := readConf(p.conf)
	if ip := ifaceIPv4(c["IFACE"]); ip != "" {
		return ip
	}
	if ip := ifaceIPv4(defaultRouteIface()); ip != "" {
		return ip
	}
	return ""
}

type nowResp struct {
	Running   bool   `json:"running"`
	State     string `json:"state"`
	Title     string `json:"title"`
	Artist    string `json:"artist"`
	Album     string `json:"album"`
	Duration  int    `json:"duration"`
	Position  int    `json:"position"`
	Volume    int    `json:"volume"`
	HasVolume bool   `json:"has_volume"`
	HasMedia  bool   `json:"has_media"`
	URI       string `json:"uri"`
	Error     string `json:"error,omitempty"`
}

func nowPlaying(p paths) nowResp {
	res := nowResp{Running: rendererRunning(p), State: "STOPPED"}
	if !res.Running {
		return res
	}
	host := rendererHost(p)

	info, err := soapCall(host, ctrlTransport, nsTransport, "GetTransportInfo", "")
	if err != nil {
		res.Error = err.Error()
		return res
	}
	res.State = tagValue(info, "CurrentTransportState")

	if pos, err := soapCall(host, ctrlTransport, nsTransport, "GetPositionInfo", ""); err == nil {
		res.Position = hmsToSec(tagValue(pos, "RelTime"))
		res.Duration = hmsToSec(tagValue(pos, "TrackDuration"))
	}

	// 曲名/艺人只能从 GetMediaInfo 拿 —— gmediarender 的 GetPositionInfo
	// 根本不返回 CurrentTrackMetaData（实测响应里整段缺失，不是空值）。
	if media, err := soapCall(host, ctrlTransport, nsTransport, "GetMediaInfo", ""); err == nil {
		res.URI = tagValue(media, "CurrentURI")
		res.HasMedia = res.URI != ""
		meta := tagValue(media, "CurrentURIMetaData")
		if meta != "" {
			res.Title = tagValue(meta, "dc:title")
			res.Artist = tagValue(meta, "upnp:artist")
			if res.Artist == "" {
				res.Artist = tagValue(meta, "dc:creator") // 网易云只用 dc:creator
			}
			res.Album = tagValue(meta, "upnp:album")
		}
	}

	if v, err := soapCall(host, ctrlRendering, nsRendering, "GetVolume", "<Channel>Master</Channel>"); err == nil {
		if n, e := strconv.Atoi(tagValue(v, "CurrentVolume")); e == nil {
			res.Volume = n
			res.HasVolume = true
		}
	}
	return res
}

// ---------------------------------------------------------------- 校验

func cleanName(s string) string {
	s = strings.TrimSpace(s)
	// 控制字符会让部分 App 截断或直接不显示
	var b strings.Builder
	for _, r := range s {
		if r == '\n' || r == '\r' || r == '\t' {
			b.WriteRune(' ')
			continue
		}
		if r < 0x20 || r == 0x7f {
			continue
		}
		b.WriteRune(r)
	}
	s = strings.TrimSpace(b.String())
	if runes := []rune(s); len(runes) > nameMaxRune {
		s = string(runes[:nameMaxRune])
	}
	return s
}

func containsCard(cards []soundCard, value string) bool {
	for _, c := range cards {
		if c.Value == value {
			return true
		}
	}
	return false
}

func containsIface(ifaces []ifaceInfo, name string) bool {
	for _, i := range ifaces {
		if i.Name == name {
			return true
		}
	}
	return false
}

// ---------------------------------------------------------------- HTTP

type stateResp struct {
	Running      bool   `json:"running"`
	FriendlyName string `json:"friendly_name"`
	Iface        string `json:"iface"`
	Audiodev     string `json:"audiodev"`
	UDN          string `json:"udn"`
	Version      string `json:"version"`
	Pid          int    `json:"pid"`
	ListenAddr   string `json:"listen_addr"`
}

func readVersion(manifest string) string {
	data, err := os.ReadFile(manifest)
	if err != nil {
		return "unknown"
	}
	for _, line := range strings.Split(string(data), "\n") {
		k, v, ok := strings.Cut(line, "=")
		if ok && strings.TrimSpace(k) == "version" {
			return strings.TrimSpace(v)
		}
	}
	return "unknown"
}

func writeJSON(w http.ResponseWriter, code int, v interface{}) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(code)
	json.NewEncoder(w).Encode(v)
}

func main() {
	log.SetFlags(log.LstdFlags)
	p := resolvePaths()

	bind := os.Getenv("DLNA_BIND")
	if bind == "" {
		// 默认绑默认路由那张网卡的 IPv4，不绑 0.0.0.0：
		// fnOS 会下发公网 IPv6，全绑等于把 root 权限的接口暴露到外面。
		if name := defaultRouteIface(); name != "" {
			bind = ifaceIPv4(name)
		}
	}
	if bind == "" {
		bind = "127.0.0.1"
	}
	port := os.Getenv("DLNA_PORT")
	if port == "" {
		port = defaultPort
	}
	addr := net.JoinHostPort(bind, port)

	mux := http.NewServeMux()

	mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/" {
			http.NotFound(w, r)
			return
		}
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		w.Header().Set("Cache-Control", "no-store")
		w.Write(indexHTML)
	})

	mux.HandleFunc("/api/state", func(w http.ResponseWriter, r *http.Request) {
		c := readConf(p.conf)
		writeJSON(w, 200, stateResp{
			Running:      rendererRunning(p),
			FriendlyName: c["FRIENDLY_NAME"],
			Iface:        c["IFACE"],
			Audiodev:     c["AUDIODEV"],
			UDN:          c["UDN"],
			Version:      readVersion(p.manifest),
			Pid:          rendererPID(p.pidFile),
			ListenAddr:   addr,
		})
	})

	mux.HandleFunc("/api/devices", func(w http.ResponseWriter, r *http.Request) {
		cards := listCards()
		ifaces := listIfaces()
		if cards == nil {
			cards = []soundCard{}
		}
		if ifaces == nil {
			ifaces = []ifaceInfo{}
		}
		writeJSON(w, 200, map[string]interface{}{"cards": cards, "ifaces": ifaces})
	})

	mux.HandleFunc("/api/now", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, 200, nowPlaying(p))
	})

	mux.HandleFunc("/api/control", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			writeJSON(w, 405, map[string]interface{}{"ok": false, "message": "只接受 POST"})
			return
		}
		var req struct {
			Action string `json:"action"`
			Value  int    `json:"value"`
		}
		if err := json.NewDecoder(io.LimitReader(r.Body, 4096)).Decode(&req); err != nil {
			writeJSON(w, 400, map[string]interface{}{"ok": false, "message": "请求格式不对"})
			return
		}
		if !rendererRunning(p) {
			writeJSON(w, 503, map[string]interface{}{"ok": false, "message": "渲染器没在运行"})
			return
		}
		host := rendererHost(p)

		// 动作白名单：参数一律由后端拼，前端只给名字和数字，杜绝拼 SOAP 注入
		var err error
		switch req.Action {
		case "play":
			_, err = soapCall(host, ctrlTransport, nsTransport, "Play", "<Speed>1</Speed>")
		case "pause":
			_, err = soapCall(host, ctrlTransport, nsTransport, "Pause", "")
		case "stop":
			_, err = soapCall(host, ctrlTransport, nsTransport, "Stop", "")
		case "seek":
			if req.Value < 0 || req.Value > 86400 {
				writeJSON(w, 400, map[string]interface{}{"ok": false, "message": "进度超出范围"})
				return
			}
			_, err = soapCall(host, ctrlTransport, nsTransport, "Seek",
				"<Unit>REL_TIME</Unit><Target>"+secToHMS(req.Value)+"</Target>")
		case "volume":
			if req.Value < 0 || req.Value > 100 {
				writeJSON(w, 400, map[string]interface{}{"ok": false, "message": "音量超出范围"})
				return
			}
			_, err = soapCall(host, ctrlRendering, nsRendering, "SetVolume",
				fmt.Sprintf("<Channel>Master</Channel><DesiredVolume>%d</DesiredVolume>", req.Value))
		default:
			writeJSON(w, 400, map[string]interface{}{"ok": false, "message": "不支持的操作"})
			return
		}
		if err != nil {
			writeJSON(w, 500, map[string]interface{}{"ok": false, "message": err.Error()})
			return
		}
		writeJSON(w, 200, map[string]interface{}{"ok": true})
	})

	mux.HandleFunc("/api/test-tone", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			writeJSON(w, 405, map[string]interface{}{"ok": false, "message": "只接受 POST"})
			return
		}
		var req struct {
			Audiodev string `json:"audiodev"`
		}
		if err := json.NewDecoder(io.LimitReader(r.Body, 4096)).Decode(&req); err != nil {
			writeJSON(w, 400, map[string]interface{}{"ok": false, "message": "请求格式不对"})
			return
		}
		// 白名单：设备名必须在 aplay -l 的结果里，杜绝拼参数执行别的东西
		if !containsCard(listCards(), req.Audiodev) {
			writeJSON(w, 400, map[string]interface{}{"ok": false, "message": "这个声卡不在可用列表里"})
			return
		}
		if err := playTone(req.Audiodev); err != nil {
			writeJSON(w, 500, map[string]interface{}{"ok": false, "message": err.Error()})
			return
		}
		writeJSON(w, 200, map[string]interface{}{"ok": true, "message": "已播放 2 秒 440Hz"})
	})

	mux.HandleFunc("/api/config", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			writeJSON(w, 405, map[string]interface{}{"ok": false, "message": "只接受 POST"})
			return
		}
		var req struct {
			FriendlyName string `json:"friendly_name"`
			Iface        string `json:"iface"`
			Audiodev     string `json:"audiodev"`
		}
		if err := json.NewDecoder(io.LimitReader(r.Body, 4096)).Decode(&req); err != nil {
			writeJSON(w, 400, map[string]interface{}{"ok": false, "message": "请求格式不对"})
			return
		}

		name := cleanName(req.FriendlyName)
		if name == "" {
			writeJSON(w, 400, map[string]interface{}{"ok": false, "message": "广播名不能为空"})
			return
		}
		if !containsIface(listIfaces(), req.Iface) {
			writeJSON(w, 400, map[string]interface{}{"ok": false, "message": "网卡 " + req.Iface + " 不存在"})
			return
		}
		if !containsCard(listCards(), req.Audiodev) {
			writeJSON(w, 400, map[string]interface{}{"ok": false, "message": "声卡 " + req.Audiodev + " 不存在"})
			return
		}

		// UDN 必须稳定：它是手机记住"这是同一台设备"的依据，改名不能换它
		udn := readConf(p.conf)["UDN"]
		if udn == "" {
			udn = "uuid:GMediaRender-1_0-000-000-002"
		}
		updates := map[string]string{
			"FRIENDLY_NAME": name,
			"IFACE":         req.Iface,
			"AUDIODEV":      req.Audiodev,
			"UDN":           udn,
		}
		if err := writeConf(p.conf, updates); err != nil {
			writeJSON(w, 500, map[string]interface{}{"ok": false, "message": "写配置失败: " + err.Error()})
			return
		}

		restartOut, restartErr := restartRenderer(p)
		time.Sleep(1500 * time.Millisecond)

		running := rendererRunning(p)
		advertised := ""
		if running {
			host := ifaceIPv4(req.Iface)
			if host == "" {
				host = bind
			}
			if got, err := probeFriendlyName(host); err == nil {
				advertised = got
			}
		}

		resp := map[string]interface{}{
			"ok":              running,
			"running":         running,
			"advertised_name": advertised,
		}
		switch {
		case !running:
			resp["message"] = "保存成功，但渲染器没能起来。看看下面的日志。"
			resp["log_tail"] = tailFile(p.renderer, 25)
			if restartOut != "" {
				resp["restart_output"] = strings.TrimSpace(restartOut)
			}
			if restartErr != nil {
				resp["restart_error"] = restartErr.Error()
			}
		case advertised != "" && advertised != name:
			resp["message"] = "渲染器已启动，但广播名还是「" + advertised + "」，可能被截断或转义异常。"
		case advertised == "":
			resp["message"] = "已保存并重启（渲染器在跑），但没取到设备描述，手机上可能要等 1–2 分钟。"
		default:
			resp["message"] = "已生效，手机上的设备名现在是「" + advertised + "」。"
		}
		writeJSON(w, 200, resp)
	})

	srv := &http.Server{
		Addr:              addr,
		Handler:           mux,
		ReadHeaderTimeout: 5 * time.Second,
	}
	log.Printf("dlna-console listening on http://%s", addr)
	log.Printf("conf=%s  cmd/main=%s", p.conf, p.cmdMain)
	if err := srv.ListenAndServe(); err != nil {
		log.Fatalf("监听失败: %v", err)
	}
}
