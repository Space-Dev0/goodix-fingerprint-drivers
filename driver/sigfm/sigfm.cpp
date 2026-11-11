// SIGFM algorithm for libfprint

// Copyright (C) 2022 Matthieu CHARETTE <matthieu.charette@gmail.com>
// Copyright (c) 2022 Natasha England-Elbro <ashenglandelbro@protonmail.com>
// Copyright (c) 2022 Timur Mangliev <tigrmango@gmail.com>

// This library is free software; you can redistribute it and/or
// modify it under the terms of the GNU Lesser General Public
// License as published by the Free Software Foundation; either
// version 2.1 of the License, or (at your option) any later version.

// This library is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
// Lesser General Public License for more details.

// You should have received a copy of the GNU Lesser General Public
// License along with this library; if not, write to the Free Software
// Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
//

#include "sigfm.hpp"
#include "binary.hpp"
#include "img-info.hpp"

#include <opencv2/core.hpp>
#include <opencv2/imgproc.hpp>
#include <opencv2/features2d.hpp>
#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <limits>
#include <mutex>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <tuple>
#include <vector>
__attribute__((constructor(101)))
static void sigfm_init_env()
{
    setenv("TBB_MALLOC_USE_HUGE_PAGES", "0", 1);
    setenv("OPENCV_FORBID_NUMA", "1", 1);
    setenv("OPENCV_NUM_THREADS", "1", 1);
}

namespace {
static std::once_flag sigfm_thread_flag;
/* Templates use a fixed-size OpenCV keypoint record followed by 128-float
 * SIFT descriptors. Bound the keypoint count before reserving persisted data.
 */
constexpr std::size_t serialized_keypoint_size = sizeof(int) * 2 + sizeof(float) * 5;
constexpr std::size_t max_serialized_keypoints = 2048;
constexpr int sift_descriptor_cols = 128;
constexpr int sift_descriptor_type = CV_32F;
} // namespace

namespace bin {

template<>
struct serializer<SigfmImgInfo> : public std::true_type {
    static void serialize(const SigfmImgInfo& info, stream& out)
    {
        out << info.keypoints << info.descriptors;
    }
};

template<>
struct deserializer<SigfmImgInfo> : public std::true_type {
    static SigfmImgInfo deserialize(stream& in)
    {
        SigfmImgInfo info;

        std::size_t keypoint_count;
        in >> keypoint_count;
        if (keypoint_count > max_serialized_keypoints ||
            keypoint_count > in.size() / serialized_keypoint_size) {
            throw std::runtime_error{"invalid SIGFM keypoint count"};
        }

        info.keypoints.reserve(keypoint_count);
        for (std::size_t i = 0; i < keypoint_count; i++) {
            cv::KeyPoint keypoint;
            in >> keypoint;
            info.keypoints.emplace_back(std::move(keypoint));
        }

        int type, rows, cols;
        in >> type >> rows >> cols;
        if (type != sift_descriptor_type || rows < 0 || cols != sift_descriptor_cols ||
            static_cast<std::size_t>(rows) != keypoint_count) {
            throw std::runtime_error{"invalid SIGFM descriptor metadata"};
        }

        const auto descriptor_bytes = keypoint_count * sift_descriptor_cols * sizeof(float);
        if (descriptor_bytes > in.size()) {
            throw std::runtime_error{"invalid SIGFM descriptor data"};
        }

        info.descriptors.create(rows, cols, type);
        in.read(info.descriptors.data, descriptor_bytes);
        return info;
    }
};
} // namespace bin

namespace {
/* The 80x64 581a captures produce few distinctive SIFT descriptors across
 * separate presses. Keep more Lowe-ratio candidates here; the reciprocal
 * match and geometric-consistency stages below still reject outliers.
 */
constexpr auto distance_match = 0.90;
/* Compare pairwise scales with the robust common scale for the press. This
 * allows a consistent change in apparent finger size while still rejecting
 * local distortions that do not fit one global transform.
 */
constexpr auto length_match = 0.20;
constexpr auto min_pair_scale = 0.70;
constexpr auto max_pair_scale = 1.43;
constexpr auto angle_match = 0.12;
constexpr auto min_match = 4;
constexpr auto sift_nfeatures = 0;
constexpr auto sift_octave_layers = 3;
constexpr auto sift_contrast_threshold = 0.04;
constexpr auto sift_edge_threshold = 18.0;
constexpr auto sift_sigma = 1.2;
struct match {
    cv::Point2f p1;
    cv::Point2f p2;
    match(cv::Point2f ip1, cv::Point2f ip2) : p1{ip1}, p2{ip2} {}
    match() : p1{cv::Point2f(0, 0)}, p2{cv::Point2f(0, 0)} {}
    bool operator==(const match& right) const
    {
        return std::tie(this->p1, this->p2) == std::tie(right.p1, right.p2);
    }
    bool operator<(const match& right) const
    {
        return std::tie(this->p1.y, this->p1.x, this->p2.y, this->p2.x) <
               std::tie(right.p1.y, right.p1.x, right.p2.y, right.p2.x);
    }
};
struct angle {
    double radians;
    angle(double radians_) : radians{radians_} {}
};
} // namespace

SigfmImgInfo* sigfm_copy_info(SigfmImgInfo* info)
{
    if (info == nullptr) {
        return nullptr;
    }
    try {
        return new SigfmImgInfo{*info};
    }
    catch (...) {
        return nullptr;
    }
}

int sigfm_keypoints_count(SigfmImgInfo* info)
{
    /* sigfm_extract() reports failure with nullptr and the C callers in
     * goodix53x5-match.c hand the result straight to this function before any
     * null check, so treat it as "no keypoints" rather than dereferencing. */
    if (info == nullptr) {
        return 0;
    }
    return info->keypoints.size();
}

int sigfm_descriptors_count(SigfmImgInfo* info)
{
    if (info == nullptr) {
        return 0;
    }
    return info->descriptors.rows;
}

unsigned char* sigfm_serialize_binary(SigfmImgInfo* info, int* outlen)
{
    if (outlen != nullptr) {
        *outlen = 0;
    }
    if (info == nullptr || outlen == nullptr) {
        return nullptr;
    }

    try {
        bin::stream s;
        s << *info;
        if (s.size() > static_cast<std::size_t>(std::numeric_limits<int>::max())) {
            return nullptr;
        }
        auto* buffer = s.copy_buffer();
        if (buffer != nullptr) {
            *outlen = static_cast<int>(s.size());
        }
        return buffer;
    }
    catch (...) {
        return nullptr;
    }
}

SigfmImgInfo* sigfm_deserialize_binary(const unsigned char* bytes, int len)
{
    if (bytes == nullptr || len <= 0) {
        return nullptr;
    }

    try {
        bin::stream s{bytes, bytes + len};
        auto info = std::make_unique<SigfmImgInfo>();
        s >> *info;
        if (s.size() != 0) {
            return nullptr;
        }
        return info.release();
    }
    catch (const std::exception&) {
        return nullptr;
    }
}

SigfmImgInfo* sigfm_extract(const SigfmPix* pix, int width, int height)
{
    /* cv::Mat::create() accepts non-positive dimensions without complaint and
     * leaves the Mat in a state where the memcpy below corrupts the heap; the
     * throw only surfaces later, inside CLAHE. Reject the dimensions up front
     * rather than relying on OpenCV to catch them. */
    if (pix == nullptr || width <= 0 || height <= 0) {
        return nullptr;
    }

    /* This function is called across the C ABI from the driver's C state-machine
     * handlers (via goodix_match_extract()). An OpenCV cv::Exception or a
     * std::bad_alloc unwinding through a C stack frame is undefined behaviour
     * and reaches std::terminate(), killing the root fprintd process. Report
     * failure with nullptr instead, matching sigfm_match_score() below. */
    try {
        std::call_once(sigfm_thread_flag, []() {
            cv::setNumThreads(1);
        });

        cv::Mat img;
        img.create(height, width, CV_8UC1);
        std::memcpy(img.data, pix, (std::size_t) width * (std::size_t) height);

        /* Apply CLAHE to enhance local contrast for better SIFT detection */
        auto clahe = cv::createCLAHE(2.0, cv::Size(8, 8));
        cv::Mat enhanced;
        clahe->apply(img, enhanced);

        const auto roi = cv::Mat::ones(cv::Size{enhanced.size[1], enhanced.size[0]}, CV_8UC1);
        std::vector<cv::KeyPoint> pts;

        cv::Mat descs;
        cv::SIFT::create(sift_nfeatures,
                         sift_octave_layers,
                         sift_contrast_threshold,
                         sift_edge_threshold,
                         sift_sigma)
            ->detectAndCompute(enhanced, roi, pts, descs);

        auto* info = new SigfmImgInfo{pts, descs};
        return info;
    }
    catch (...) {
        return nullptr;
    }
}

int sigfm_match_score(SigfmImgInfo* frame, SigfmImgInfo* enrolled,
                      const char* context)
{
    const char* label = context ? context : "unlabeled stage";
    if (frame == nullptr || enrolled == nullptr || frame->descriptors.empty() ||
        enrolled->descriptors.empty() ||
        frame->keypoints.size() != static_cast<std::size_t>(frame->descriptors.rows) ||
        enrolled->keypoints.size() != static_cast<std::size_t>(enrolled->descriptors.rows)) {
        std::fprintf(stderr, "SIGFM match [%s]: invalid feature data (probe=%d, template=%d)\n",
                     label,
                     frame ? frame->descriptors.rows : 0,
                     enrolled ? enrolled->descriptors.rows : 0);
        return 0;
    }

    try {
        /* Lowe's ratio rejects ambiguous descriptors. The reverse one-nearest
         * pass below keeps only reciprocal pairs before geometric scoring.
         */
        std::vector<std::vector<cv::DMatch>> points;
        auto bfm = cv::BFMatcher::create();
        bfm->knnMatch(frame->descriptors, enrolled->descriptors, points, 2);
        std::vector<int> candidate_positions(enrolled->descriptors.rows, -1);
        std::vector<int> candidate_indices;
        std::vector<double> descriptor_ratios;
        cv::Mat candidate_descriptors;
        int ratio_passes = 0;

        for (const auto& pts : points) {
            if (pts.size() < 2) {
                continue;
            }

            const cv::DMatch& match_1 = pts.at(0);
            const double second_distance = pts.at(1).distance;
            const double ratio = second_distance > 0.0
                                     ? match_1.distance / second_distance
                                     : std::numeric_limits<double>::infinity();
            descriptor_ratios.push_back(ratio);
            if (match_1.distance < distance_match * pts.at(1).distance) {
                ratio_passes++;
                if (candidate_positions[match_1.trainIdx] < 0) {
                    candidate_positions[match_1.trainIdx] = candidate_indices.size();
                    candidate_indices.push_back(match_1.trainIdx);
                    candidate_descriptors.push_back(enrolled->descriptors.row(match_1.trainIdx));
                }
            }
        }

        double ratio_median = 0.0;
        double ratio_p90 = 0.0;
        if (!descriptor_ratios.empty()) {
            std::sort(descriptor_ratios.begin(), descriptor_ratios.end());
            ratio_median = descriptor_ratios[descriptor_ratios.size() / 2];
            ratio_p90 = descriptor_ratios[(descriptor_ratios.size() - 1) * 90 / 100];
        }

        std::fprintf(stderr,
                     "SIGFM match [%s]: descriptors probe=%d template=%d knn=%zu ratio-pass=%d unique-candidates=%zu ratio-p50=%.3f p90=%.3f threshold=%.2f\n",
                     label, frame->descriptors.rows, enrolled->descriptors.rows,
                     points.size(), ratio_passes, candidate_indices.size(),
                     ratio_median, ratio_p90, distance_match);

        if (candidate_indices.size() < min_match) {
            std::fprintf(stderr, "SIGFM match [%s]: only %zu ratio-test candidates (need %d)\n",
                         label, candidate_indices.size(), min_match);
            return 0;
        }

        std::vector<std::vector<cv::DMatch>> backward;
        bfm->knnMatch(candidate_descriptors, frame->descriptors, backward, 1);
        std::set<match> matches_unique;
        int nb_matched = 0;
        for (const auto& pts : points) {
            if (pts.size() < 2) {
                continue;
            }
            const cv::DMatch& match_1 = pts.at(0);
            if (match_1.distance < distance_match * pts.at(1).distance) {
                const int candidate_position = candidate_positions[match_1.trainIdx];
                if (candidate_position < 0 || backward[candidate_position].empty() ||
                    backward[candidate_position][0].trainIdx != match_1.queryIdx) {
                    continue;
                }

                matches_unique.emplace(
                    match{frame->keypoints.at(match_1.queryIdx).pt,
                          enrolled->keypoints.at(match_1.trainIdx).pt});
                nb_matched++;
            }
        }
        if (nb_matched < min_match) {
            std::fprintf(stderr, "SIGFM match [%s]: reciprocal=%d from %zu unique candidates (need %d)\n",
                         label, nb_matched, candidate_indices.size(), min_match);
            return 0;
        }
        std::vector<match> matches{matches_unique.begin(),
                                   matches_unique.end()};

        /* Compare pairwise displacement vectors instead of absolute points,
         * so translation is ignored. Estimate one common scale for this press
         * before filtering pairs: skin pressure can change apparent finger
         * size, while inconsistent local scales still indicate bad matches.
         */
        struct pair_geometry {
            double scale_ratio;
            double rotation;
        };
        std::vector<pair_geometry> pair_geometries;
        for (std::size_t j = 0; j < matches.size(); j++) {
            match match_1 = matches[j];
            for (std::size_t k = j + 1; k < matches.size(); k++) {
                match match_2 = matches[k];

                const double x1 = match_1.p1.x - match_2.p1.x;
                const double y1 = match_1.p1.y - match_2.p1.y;
                const double x2 = match_1.p2.x - match_2.p2.x;
                const double y2 = match_1.p2.y - match_2.p2.y;

                const double length_1 = std::hypot(x1, y1);
                const double length_2 = std::hypot(x2, y2);

                if (length_1 < 3.0 || length_2 < 3.0)
                    continue;

                const double dot = x1 * x2 + y1 * y2;
                const double cross = x1 * y2 - y1 * x2;
                pair_geometries.push_back({length_2 / length_1,
                                           std::atan2(cross, dot)});
            }
        }

        if (pair_geometries.size() < min_match) {
            std::fprintf(stderr, "SIGFM match [%s]: reciprocal=%d unique=%zu usable-vectors=%zu (need %d)\n",
                         label, nb_matched, matches.size(),
                         pair_geometries.size(), min_match);
            return 0;
        }

        std::vector<double> scale_ratios;
        scale_ratios.reserve(pair_geometries.size());
        for (const auto& geometry : pair_geometries)
            scale_ratios.push_back(geometry.scale_ratio);

        std::sort(scale_ratios.begin(), scale_ratios.end());
        const double scale_p10 = scale_ratios[(scale_ratios.size() - 1) * 10 / 100];
        const double median_scale = scale_ratios[scale_ratios.size() / 2];
        const double scale_p90 = scale_ratios[(scale_ratios.size() - 1) * 90 / 100];
        if (median_scale < min_pair_scale || median_scale > max_pair_scale) {
            std::fprintf(stderr, "SIGFM match [%s]: implausible scale p10=%.3f median=%.3f p90=%.3f allowed=%.2f..%.2f\n",
                         label, scale_p10, median_scale, scale_p90,
                         min_pair_scale, max_pair_scale);
            return 0;
        }

        std::vector<angle> angles;
        for (const auto& geometry : pair_geometries) {
            const double scale_similarity =
                1.0 - std::min(geometry.scale_ratio, median_scale) /
                          std::max(geometry.scale_ratio, median_scale);
            if (scale_similarity <= length_match)
                angles.emplace_back(geometry.rotation);
        }

        if (angles.size() < min_match) {
            std::fprintf(stderr, "SIGFM match [%s]: vectors=%zu scale-p10=%.3f median=%.3f p90=%.3f scale-consistent=%zu (need %d) tolerance=%.2f\n",
                         label, pair_geometries.size(), scale_p10, median_scale,
                         scale_p90, angles.size(), min_match, length_match);
            return 0;
        }

        int best_rotation_support = 0;
        double dominant_rotation = 0.0;
        for (const auto& candidate_angle : angles) {
            int support = 0;
            for (const auto& other_angle : angles) {
                const double delta = std::remainder(candidate_angle.radians -
                                                        other_angle.radians,
                                                    2.0 * M_PI);
                if (std::abs(delta) <= angle_match)
                    support++;
            }
            if (support > best_rotation_support) {
                best_rotation_support = support;
                dominant_rotation = candidate_angle.radians;
            }
        }

        int count = 0;
        for (std::size_t j = 0; j < angles.size(); j++) {
            const angle& angle_1 = angles[j];
            for (std::size_t k = j + 1; k < angles.size(); k++) {
                const angle& angle_2 = angles[k];
                const double delta = std::remainder(angle_1.radians - angle_2.radians,
                                                    2.0 * M_PI);
                if (std::abs(delta) <= angle_match) {
                    count += 1;
                }
            }
        }
        std::fprintf(stderr, "SIGFM match [%s]: ratio-passes=%d unique-candidates=%zu reciprocal=%d unique=%zu scale=[%.3f,%.3f,%.3f] consistent=%zu rotation-support=%d/%zu at %.1fdeg threshold=%.3frad score=%d\n",
                     label, ratio_passes, candidate_indices.size(), nb_matched,
                     matches.size(), scale_p10, median_scale, scale_p90,
                     angles.size(), best_rotation_support, angles.size(),
                     dominant_rotation * 180.0 / M_PI, angle_match, count);
        return count;
    }
    catch (const cv::Exception& e) {
        std::fprintf(stderr, "SIGFM match [%s]: OpenCV matching failed: %s\n",
                     label, e.what());
        return -1;
    }
    catch (const std::exception& e) {
        std::fprintf(stderr, "SIGFM match [%s]: matching failed: %s\n",
                     label, e.what());
        return -1;
    }
    catch (...) {
        std::fprintf(stderr, "SIGFM match [%s]: matching failed with an unknown exception\n",
                     label);
        return -1;
    }
}

void sigfm_free_info(SigfmImgInfo* info) { delete info; }
